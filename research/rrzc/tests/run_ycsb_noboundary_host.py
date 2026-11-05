#!/usr/bin/env python3
"""独立关闭边界补足估价，先做F消融，再做B读暴露配对；不覆盖旧实验。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import signal
import socket

import run_ycsb_quick_host as quick
from run_ycsb_headroom_host import HeadroomRun, PARENT, SOURCE, RECORDS
from run_ycsb_af_host import host_resources
from run_joint_host import digest_manifest
from run_tail_ablation_fixed_pair import running_parent
from ycsb_af import digest, require, LABELS

LABEL = 'joint-ycsb8-noboundary-v1-20261023'
SCHEDULE = (('f', 300000), ('b', 1200000))


def wrapper_source():
    text = quick.wrapper_source()
    text = quick.replace_once(text, 'ZENFS_RRZC_TERMINAL="$enabled"', 'ZENFS_RRZC_TERMINAL=0')
    return quick.replace_once(text, '"$mode" "$enabled" "$enabled" > "$result/策略.txt"',
                              '"$mode" "$enabled" 0 > "$result/策略.txt"')


def analysis_source():
    text = quick.analysis_source()
    text = quick.replace_once(text,
        "'边界配置：版本=2，启用=' + str(int(enabled)) + '，'", "'边界配置：版本=2，启用=0，'")
    text = quick.replace_once(text, 'terminal_audit(lines, version=2) if enabled else space_audit(lines)',
                              'space_audit(lines)')
    text = quick.replace_once(text, 'terminal=enabled, terminal_version=2', 'terminal=False, terminal_version=2')
    text = quick.replace_once(text,
        "sum(p.get('实际Zone') != p.get('RR项置零Zone') for p in plans) if enabled else None",
        'rr_changes(lines) if enabled else None')
    text = quick.replace_once(text, '策略名称=LABELS[mode]',
        "策略名称=('去边界联合成本模型' if enabled else LABELS[mode])")
    # 在同一候选帧中将概率、RR复制及RR空间同时置零；不是与另一试次的Zone地址硬比。
    return text + '''

def rr_changes(lines):
    from analyze_deadline import choose
    from analyze_space_plan import demand
    groups = {}
    for line in lines:
        for tag in ('时序成本', '时序方案需求', '时序方案决策'):
            if tag+'：' in line:
                row = fields(line.split('，原因=', 1)[0], tag+'：')
                groups.setdefault(int(row['轮次']), {}).setdefault(tag, []).append(row)
    changed = 0
    for frame in groups.values():
        require(len(frame.get('时序方案需求', [])) == len(frame.get('时序方案决策', [])) == 1,
                '同帧置零复算需求及决策不闭合')
        d = frame['时序方案需求'][0]
        need = demand(*(int(d[k]) for k in ('空闲', '非空闲', '分配等待', '目标百分比')))
        rows = [dict(z=int(r['Zone']), v=int(r['有效']), cap=int(r['容量']),
                     safe=int(r['安全']), p=0, credit=0, space=0)
                for r in frame.get('时序成本', [])]
        _, zero, _ = choose(rows, need)
        actual = frame['时序方案决策'][0]
        selected = int(actual['实际Zone']) if int(actual['实际存在']) else None
        changed += selected != zero
    return changed
'''


def freeze(output, profile):
    quick.freeze(output, profile)
    frozen = output/'源码'
    (frozen/'run_ycsb_quick_guest.sh').write_text(wrapper_source())
    (frozen/'analyze_ycsb_quick.py').write_text(analysis_source())
    plot = quick.plot_source().replace('YCSB-F，1572864键/30万请求', 'YCSB-F/B，4GiB装载，F30万/B120万请求')
    plot = plot.replace('边界第二版', '去边界联合成本版')
    plot = plot.replace('from ycsb_af import WORKLOADS, LABELS, require, digest',
        "from ycsb_af import WORKLOADS, LABELS, require, digest\nLABELS = {**LABELS, 'model': '去边界联合成本模型'}")
    (frozen/'plot_ycsb_quick.py').write_text(plot)
    expected = json.loads((SOURCE/'同库身份.json').read_text())
    target = '/home/femu/'+quick.SOURCE.name.removesuffix('-results')
    expected[target+'/run_ycsb_quick_guest.sh'] = digest(frozen/'run_ycsb_quick_guest.sh')
    quick.manifest(output, expected)
    for source in (Path(__file__), quick.ROOT/'tests/test_ycsb_noboundary.py'):
        shutil.copyfile(source, frozen/source.name)
    (output/'调整说明.json').write_text(json.dumps(dict(
        软件唯一变化='业务包装器两组均TERMINAL=0；模型仍启用RR预测和留存，空间对照仍关闭RR评分',
        源码变更='独立实验及核验入口；旧Baseline、旧80/20和边界版实现及结果不变',
        负载='F沿用4GiB装载30万请求；B为标准95%READ/5%UPDATE，4GiB装载120万请求',
        控制='各负载两组请求、种子、软件、设备和初态一致；F与B之间不是配对',
        不变='RR1024、GC20%、候选资格、分配路径、严格计数、无WAL/缓存、单线程、不限速',
        来源同库身份SHA256=digest(SOURCE/'同库身份.json'),
        新包装器SHA256=digest(frozen/'run_ycsb_quick_guest.sh')),
        ensure_ascii=False, indent=2))


class NoBoundaryRun(HeadroomRun):
    def __init__(self, *args):
        super().__init__(*args)
        self.state.update(模式顺序=[f'{s}-{w}-{m}' for w, _ in SCHEDULE
            for s in ('smoke', 'full') for m in LABELS],
            边界='去边界消融；F/B分别新配对，单轮非稳态，不预设收益')

    def arm(self, stage, mode, analyzer, letter='f', operations=300000):
        tag = f'{stage}-{letter}-{mode}'
        records, ops = (8192, 8192) if stage == 'smoke' else (RECORDS, operations)
        self.parent_image = quick.IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out = self.output/tag
        out.mkdir()
        remote = '/home/femu/rrzc-sst-results/'+tag
        error = None
        try:
            self.start(tag)
            expected = json.loads((self.output/'同库身份.json').read_text())
            require(digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected), 180)) == expected,
                    '真实部署软件身份漂移')
            self.root(f'nohup setsid bash {self.target}/run_ycsb_af_guest.sh {tag} {mode} {letter} '
                      f'{records} {ops} 20261023 < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：真实不限速业务已启动', 客机结果目录=remote, 计划业务请求数=ops)
            self.observe(tag)
        except BaseException as cause:
            error = cause
            self.record(原始业务错误=type(cause).__name__+'：'+str(cause))
            raise
        finally:
            try:
                self.finish(out, remote)
            except Exception as cleanup:
                if error is None:
                    raise
                self.record(收尾另有错误=str(cleanup))
        result = analyzer.analyze_arm(out/tag, tag, letter, mode, records, ops)
        (out/'独立核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        self.state['已完成'].append(tag)
        self.record(阶段=tag+'：数据、维护、评分及正常关机验收通过',
            最近结果={k:result[k] for k in ('RR次数', 'ZC次数', '总迁移字节', '前台吞吐ops每秒')})


def preflight(args):
    require(args.allow_unlocked and args.label == LABEL, '须显式未锁页且使用本次独立标签')
    output = quick.IMAGE_DIR/(args.label+'-results')
    require(not output.exists() and PARENT.is_file() and not running_parent(PARENT), '结果存在或父镜像不可用')
    require(os.access('/dev/kvm', os.R_OK|os.W_OK), 'KVM不可用')
    for tag in ('build', 'smoke-f-space', 'smoke-f-model'):
        archive = json.loads((SOURCE/tag/'阶段归档.json').read_text())
        require(archive['完整'] and not archive['客机仍存活'], '来源未归档退出：'+tag)
    for tag in ('build', *[f'{s}-{w}-{m}' for w, _ in SCHEDULE for s in ('smoke', 'full') for m in LABELS]):
        name = args.label+'-'+tag
        for path in (quick.IMAGE_DIR/(name+'.qcow2'), Path('/tmp')/(name+'.pid'),
                     Path('/tmp')/(name+'.qmp'), Path('/tmp')/(name+'-ssh.sock')):
            require(not path.exists(), '拒绝覆盖新目标：'+str(path))
    with socket.socket() as sock:
        require(sock.connect_ex(('127.0.0.1', 8096)) != 0, '8096端口被占用')
    binary = digest(quick.PROJECT/'build-femu/qemu-system-x86_64')
    require(binary == json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'], 'FEMU身份漂移')
    profile = quick.profile8()
    require(profile == json.loads((SOURCE/'平台配置.json').read_text()), '设备配置改变')
    return output, profile, binary, dict(宿主=host_resources(profile), 磁盘=quick.disk_space(initial=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default=LABEL)
    parser.add_argument('--allow-unlocked', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    output, profile, binary, budget = preflight(args)
    if args.check:
        print(json.dumps(dict(预检通过=True, 资源=budget, 正式安排=SCHEDULE), ensure_ascii=False, indent=2))
        return
    password = getpass.getpass('去边界F/B配对认证（仅内存）：')
    output.mkdir()
    freeze(output, profile)
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='消除零RR时边界估价导致的额外改选，再以读多写少B检验真实RR节省；不筛选正收益',
        FEMU二进制SHA256=binary, 初始资源=budget, 父镜像=str(PARENT),
        装载键数=RECORDS, 正式安排=SCHEDULE, 小测='F/B各8192键及8192请求，两组均验收',
        指标='API均值和P99/P99.9/P99.99、前台及Run吞吐、RR/ZC/迁移/Reset和推导擦除',
        安全='单台串行；原磁盘和内存保护不降低；失败归档正常关机并停止，不自动重跑',
        解释='新鲜配对，不复用旧正式基线；旧边界F仅作为跨试次消融参考，不能归因全部波动'),
        ensure_ascii=False, indent=2))
    if args.detach:
        reader, writer = os.pipe()
        pid = os.fork()
        if pid:
            os.close(writer)
            require(os.read(reader, 1) == b'1', '监督分离失败')
            os.close(reader)
            print(f'去边界F/B监督PID={pid}，状态={output/"状态.json"}', flush=True)
            return
        os.close(reader)
        os.setsid()
        fd = os.open(output/'监督.log', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        os.dup2(fd, 1); os.dup2(fd, 2); os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
        os.write(writer, b'1'); os.close(writer)
    run = NoBoundaryRun(args, password, output, profile)
    run.qemu_digest = binary
    def stop(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        analyzer = quick.module_from(output/'源码/analyze_ycsb_quick.py', 'noboundary_analyzer')
        run.build()
        for letter, operations in SCHEDULE:
            for stage in ('smoke', 'full'):
                for mode in LABELS:
                    run.arm(stage, mode, analyzer, letter, operations)
                records, ops = (8192, 8192) if stage == 'smoke' else (RECORDS, operations)
                pair = analyzer.compare_pair(output, stage, letter, records, ops)
                (output/f'{stage}-{letter}-配对核验.json').write_text(json.dumps(pair, ensure_ascii=False, indent=2))
                run.record(阶段=f'{stage}-{letter}配对核验通过，客机已关闭', 最近差值=pair['差值'])
                if stage == 'full':
                    quick.module_from(output/'源码/plot_ycsb_quick.py', 'noboundary_plot').render(output)
        run.record(阶段='去边界F/B实验及图表完成，全部客机已关闭', 图表目录=str(output/'图表'))
    except BaseException as error:
        run.record(阶段='去边界实验停止，原始错误保留，不自动重跑', 错误=type(error).__name__+'：'+str(error))
        raise
    finally:
        run.password = None


if __name__ == '__main__':
    main()
