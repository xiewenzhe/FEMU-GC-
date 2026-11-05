#!/usr/bin/env python3
"""复用已通过短测的停止镜像，串行运行两臂各400万请求；不重编译或改变机制。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import signal

import run_joint_host as base
from run_joint_host import require, PROJECT, IMAGE_DIR, digest_manifest
from run_rr_fair_ycsb_host import FairRun, interrupted
from analyze_stable_ycsb import sizes, analyze_arm, compare_pair

LABEL = 'joint-rr-fair-long-v1-20261021'
SOURCE = IMAGE_DIR/'joint-rr-fair-ycsb-v1-20261021-results'
PARENT = IMAGE_DIR/'joint-rr-fair-ycsb-v1-20261021-build.qcow2'
MANIFEST = SOURCE/'同库身份.json'
TARGET = '/home/femu/joint-rr-fair-ycsb-v1-20261021'


def checked_source():
    """重新读取原证据；失败不得跳过短测直接进入长测。"""
    state = json.loads((SOURCE/'状态.json').read_text())
    require(state['当前客机PID'] is None, '短测尚未完成关机')
    for tag in ('rr-build', 'build', 'smoke-greedy', 'smoke-stable', 'short-greedy', 'short-stable'):
        require(tag in state['已完成'], '缺少已通过阶段：'+tag)
        archive = json.loads((SOURCE/tag/'阶段归档.json').read_text())
        require(archive['完整'] and not archive['客机仍存活'], '源阶段归档/关机未完成：'+tag)
    expected = json.loads(MANIFEST.read_text())
    # 不以本地新文件替换已验证客机程序；发现漂移时停止，而不是静默重编译。
    for name in ('run_stable_ycsb_guest.sh', 'workload-stable-unlimited.properties'):
        actual = hashlib.sha256((PROJECT/'research/rrzc/tests'/name).read_bytes()).hexdigest()
        require(actual == expected[TARGET+'/'+name], '当前客机入口/负载与通过短测的版本不同：'+name)
    # 接线inc在客机按已归档补丁分层生成，不能与本地未打补丁模板直接比哈希；启动后核验其最终身份。
    for name in ('rrzc_direct_selector.h', 'rrzc_recent_eta_selector.h', 'rrzc_stable_credit_selector.h',
                 'rrzc_rr_fair_queue.h'):
        actual = hashlib.sha256((PROJECT/'research/rrzc/model'/name).read_bytes()).hexdigest()
        require(actual == expected['plugin/zenfs/fs/'+name], '模型或RR队列源码已漂移：'+name)
    result = compare_pair(SOURCE, 'short')
    for mode in ('greedy', 'stable'):
        root = SOURCE/('short-'+mode)/('short-'+mode)
        require((root/'RR调度.txt').read_text().strip() == '等待优先开关=1', '源短测未一致启用RR修复')
    return expected, result


class LongRun(FairRun):
    def __init__(self, *args):
        super().__init__(*args)
        self.target = TARGET
        self.parent_image = PARENT
        self.state.update(边界='4GiB真实不限速YCSB长对照；非32GiB全规模，不预设稳态或收益',
                          模式顺序=['long-greedy', 'long-stable'], 停止父镜像=str(PARENT),
                          客机程序目录=TARGET, 每组业务请求数=sizes('long')[1])

    def arm(self, stage, mode):
        require(stage == 'long' and mode in ('greedy', 'stable'), '只允许已登记长测模式')
        tag = stage+'-'+mode
        # 标签用于新子镜像，程序路径和父镜像固定为通过短测的版本，不能按新标签猜测构建路径。
        self.parent_image = PARENT
        out = self.output/tag
        out.mkdir()
        remote = '/home/femu/rrzc-sst-results/'+tag
        expected = json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(tag)
            raw = self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected))
            require(digest_manifest(raw) == expected, '长测启动前源码、库或业务程序身份不符')
            # 继承FairRun.root，只对业务启动显式加入共享RR公平开关；不改程序本身。
            self.root(f'nohup setsid bash {self.target}/run_stable_ycsb_guest.sh {tag} {mode} {sizes(stage)[1]} '
                      f'< /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：不限速400万请求及独立全键校验', 客机结果目录=remote,
                        计划业务请求数=sizes(stage)[1])
            self.observe(tag)
        finally:
            self.finish(out, remote)
        root = out/tag
        result = analyze_arm(root, stage, mode)
        require((root/'RR调度.txt').read_text().strip() == '等待优先开关=1', '长测RR修复开关不符')
        zen = (root/'zenfs.run.log').read_text()
        require('RR公平队列配置：启用=1，每轮最多维护=1，原阈值=1024，未改变检查周期=1' in zen,
                '未确认长测实际进入RR公平分支')
        require(zen.count('RR公平队列服务：') == zen.count('RR 开始：'), 'RR排队服务账目不闭合')
        (out/'独立核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        self.state['已完成'].append(tag)
        self.record(阶段=tag+'原健康/同块/评分/数据核验通过，已归档关机',
                    最近结果={n: result[n] for n in ('ZC次数', 'RR次数', '总迁移字节', '总成功ZoneReset', '吞吐量')})


def freeze(output, expected, short_pair, profile):
    (output/'同库身份.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2))
    (output/'短测重新核验.json').write_text(json.dumps(short_pair, ensure_ascii=False, indent=2))
    (output/'平台配置.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='冻结已通过短测的库和策略，验证延长不限速负载后RR、ZC及总维护成本，不新增模型参数',
        父镜像=str(PARENT), 已通过短测=str(SOURCE), 客机程序=TARGET,
        阶段=['long-greedy', 'long-stable'], 每组装载键数=491520, 每组业务请求数=4000000,
        负载='单线程同种子20261017，50/50，4KiB字段，读[0,4096)、更新[0,65536)，无WAL/无块缓存，limit.ops=0，无limit.file',
        唯一策略差异='两组均共享RR公平修复及安全路径；Greedy零RR抵扣与稳定收益直接排名开关不同',
        不变='4GiB、32个128MiB区、每区128块、RR1024、原GC20%、原候选/安全资格、额外固定回收门槛0',
        指标='RR/ZC、总迁移、成功Reset及映射推导块擦除、API均值/P99/P99.9/P99.99、Run吞吐及维护覆盖',
        验收='复核源短测及全部软件身份；各臂新NAND/RC、原30秒RR健康、全键最新值、逐行评分与维护账目；两臂请求/最终值/配置/库哈希一致',
        收尾='串行；单臂1500秒或连续180秒无进展则采证归档并正常关机，失败停止不自动重跑；关闭SSH控制进程',
        边界='非32GiB全规模，不预设稳态或正收益；qcow2不是NAND快照，初始SST物理布局不保证相同；API耗时不是原始NVMe延迟'),
        ensure_ascii=False, indent=2))
    frozen = output/'源码'
    frozen.mkdir()
    files = list((PROJECT/'research/rrzc').glob('*.py'))
    files += list((PROJECT/'research/rrzc/tests').glob('*.py'))
    files += [PROJECT/'research/rrzc/tests'/n for n in ('run_stable_ycsb_guest.sh', 'workload-stable-unlimited.properties')]
    identity = {}
    for p in files:
        relative = p.relative_to(PROJECT/'research/rrzc')
        dest = frozen/relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(p.read_bytes())
        identity[str(relative)] = hashlib.sha256(dest.read_bytes()).hexdigest()
    identity['build-femu/qemu-system-x86_64'] = hashlib.sha256((PROJECT/'build-femu/qemu-system-x86_64').read_bytes()).hexdigest()
    (frozen/'身份.json').write_text(json.dumps(identity, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default=LABEL)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    args.phase = 'build'
    args.scenarios = []
    base.PARENT, base.PARENT_MANIFEST = PARENT, MANIFEST
    output, profile = base.preflight(args)
    for mode in ('greedy', 'stable'):
        tag = 'long-'+mode
        for p in (IMAGE_DIR/f'{args.label}-{tag}.qcow2', Path(f'/tmp/{args.label}-{tag}.pid'),
                  Path(f'/tmp/{args.label}-{tag}.qmp'), Path(f'/tmp/{args.label}-{tag}-ssh.sock')):
            require(not p.exists(), '长测目标已存在，拒绝覆盖：'+str(p))
    expected, short_pair = checked_source()
    if args.check:
        print('短测原始证据、已停止父镜像、软件身份、长测路径和资源预算通过；未启动')
        return
    password = getpass.getpass('长测隔离客机认证（仅驻留内存）：')
    output.mkdir()
    freeze(output, expected, short_pair, profile)
    if args.detach:
        r, w = os.pipe()
        pid = os.fork()
        if pid:
            os.close(w)
            require(os.read(r, 1) == b'1', '监督分离失败')
            os.close(r)
            print(f'监督PID={pid}，状态={output / "状态.json"}', flush=True)
            return
        os.close(r)
        os.setsid()
        fd = os.open(output/'监督.log', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
        os.write(w, b'1')
        os.close(w)
    signal.signal(signal.SIGTERM, interrupted)
    run = LongRun(args, password, output, profile)
    try:
        run.record(阶段='已冻结软件和负载，准备串行长对照')
        for mode in ('greedy', 'stable'):
            run.arm('long', mode)
        result = compare_pair(output, 'long')
        with (output/'long配对核验.json').open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        run.record(阶段='400万请求两臂配对完成，全部客机已关闭，等待效果与稳态覆盖分析',
                   最近差值=result['模型相对Greedy差值'])
    except BaseException as error:
        run.record(阶段='停止，不自动重跑', 错误=str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__)
        raise
    finally:
        run.password = None


if __name__ == '__main__':
    main()
