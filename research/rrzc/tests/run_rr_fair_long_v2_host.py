#!/usr/bin/env python3
"""仅改监督与采证：先短对照验证，再以统一60分钟上限跑两臂不限速长测。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import run_joint_host as base
import run_rr_fair_long_host as old
from run_joint_host import require, PROJECT, IMAGE_DIR, digest_manifest
from analyze_stable_ycsb import sizes, analyze_arm, compare_pair
from analyze_ycsb_stalls import analyze_stalls

LABEL = 'joint-rr-fair-long-v2-20261021'
HELPER = PROJECT/'research/rrzc/tests/capture_ycsb_guest.py'


def business_key(point):
    """日志时间和后台维护计数都不能冒充客户端请求进展。"""
    row = point['最近状态']
    return point['装载完成'], int(row[1]) if row else None


def wait_gone(pid, seconds):
    deadline = time.monotonic()+seconds
    while Path(f'/proc/{pid}').exists() and time.monotonic() < deadline:
        time.sleep(1)
    return not Path(f'/proc/{pid}').exists()


class CollectedRun(old.LongRun):
    def __init__(self, *args):
        super().__init__(*args)
        self.control = '/home/femu/'+self.label_prefix+'-evidence'
        self.deployed = False
        self.state.update(模式顺序=['short-greedy', 'short-stable', 'long-greedy', 'long-stable'],
                          长测上限秒=3600, 无进展上限秒=180, 采证目录=self.control)

    def start(self, mode):
        self.deployed = False
        super().start(mode)
        self.ssh(f'test ! -e {self.control} && mkdir {self.control}')
        self.command(self.scp()+[str(self.output/'源码/tests/capture_ycsb_guest.py'),
                                f'femu@127.0.0.1:{self.control}/'])
        expected = hashlib.sha256((self.output/'源码/tests/capture_ycsb_guest.py').read_bytes()).hexdigest()
        require(self.ssh(f'sha256sum {self.control}/capture_ycsb_guest.py').split()[0] == expected,
                '部署的诊断脚本身份不符')
        self.ssh(f'python3 -m py_compile {self.control}/capture_ycsb_guest.py')
        self.deployed = True

    def sample_threads(self, tag):
        dest = self.control+'/'+tag+'-threads-'+str(time.time_ns())
        return self.root(f'python3 {self.control}/capture_ycsb_guest.py threads '
                         f'/home/femu/rrzc-sst-results/{tag} {self.target} --output {dest}', 25)

    def observe(self, tag):
        limit = 3600 if tag.startswith('long-') else 1500
        started = changed = sampled = time.monotonic()
        previous = None
        rehearsed = False
        while time.monotonic()-started < limit:
            point = json.loads(self.ssh(f'python3 {self.control}/capture_ycsb_guest.py progress '
                                       f'/home/femu/rrzc-sst-results/{tag} {self.target}'))
            self.record(阶段=tag+'：真实不限速业务及全键校验', 最近观测=point)
            with (self.output/'进度.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(模式=tag, **point), ensure_ascii=False)+'\n')
            require(not point['错误'], '出现真实后台错误，采证后关机，不自动重跑')
            if point['完成']:
                return
            if tag.startswith('short-') and point['装载完成'] and not rehearsed:
                # 两臂短测各演练一次业务尚未返回的完整采证；长测不重复复制日志。
                self.root(f'python3 {self.control}/capture_ycsb_guest.py capture '
                          f'/home/femu/rrzc-sst-results/{tag} {self.target} --output {self.control}/{tag}-live', 40)
                rehearsed = True
            # RR/ZC活动不能掩盖业务无进展；仍仅用请求状态维护原180秒保护。
            key = business_key(point)
            if key != previous:
                previous, changed = key, time.monotonic()
            require(time.monotonic()-changed < 180, '连续180秒业务无进展，采证归档后正常关机')
            if time.monotonic()-sampled >= 60:
                self.sample_threads(tag)
                sampled = time.monotonic()
            time.sleep(10)
        raise RuntimeError('本臂超过登记时限'+str(limit)+'秒，采证归档后正常关机')

    def close_guest(self):
        pid = self.state.get('当前客机PID')
        if not pid or not Path(f'/proc/{pid}').exists():
            return
        expected = f"file={self.state['当前镜像']},if=none,aio=native,cache=none,format=qcow2,id=hd0".encode()
        require(Path(f'/proc/{pid}').stat().st_uid == os.getuid() and
                expected in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'),
                '关机目标PID、账号或镜像不符，不发送关闭请求')
        try:
            self.shutdown()
            return
        except Exception as error:
            self.record(QMP关机反馈=str(error))
        # QMP已触发关机时，客机可能在停止业务；先等待，不重复做sudo认证。
        if wait_gone(pid, 150):
            return
        try:
            self.root('systemctl poweroff', 15)
        except Exception as error:
            # SSH随正常关机断开不能单独证明密码错误；以PID退出作最终判断。
            self.record(客机关机连接反馈=str(error))
        require(wait_gone(pid, 180), '正常关机后客机仍存活；停止后续组，不强制杀进程')

    def finish(self, out, remote=None):
        errors = []
        # 必须在任何关机请求之前保存仍在/tmp中的日志，不等待业务包装器返回。
        if self.deployed and remote:
            try:
                self.root(f'python3 {self.control}/capture_ycsb_guest.py capture {remote} '
                          f'{self.target} --output {self.control}/{self.state["当前阶段"]}-final', 40)
            except Exception as error:
                errors.append('退出前采证失败：'+str(error))
        for path in (self.target, remote, self.control if self.deployed else None):
            if path:
                try:
                    self.collect(out, path)
                except Exception as error:
                    errors.append('归档失败：'+str(error))
        if self.deployed and remote:
            try:
                local = out/Path(self.control).name/(self.state['当前阶段']+'-final')
                capture = json.loads((local/'采证.json').read_text())
                require(capture['完整'], '采证记录不完整')
                for name, record in capture['文件'].items():
                    require(hashlib.sha256((local/name).read_bytes()).hexdigest() == record['SHA256'], '采证哈希不符：'+name)
                require({'zenfs.partial.log', 'rocksdb.partial.log', 'ycsb.partial.log'} <= capture['文件'].keys(),
                        '缺少核心日志副本')
            except Exception as error:
                errors.append('归档独立复核失败：'+str(error))
        pid = self.state.get('当前客机PID')
        try:
            self.close_guest()
        except Exception as error:
            errors.append('关机失败：'+str(error))
        alive = bool(pid and Path(f'/proc/{pid}').exists())
        if not alive:
            self.record(当前客机PID=None, 当前镜像=None, 阶段='本阶段已归档且客机退出' if not errors else '采证异常，客机已退出')
            if self.socket:
                try:
                    reply = subprocess.run(['ssh', '-S', self.socket, '-O', 'exit', '-p', self.port,
                                            'femu@127.0.0.1'], capture_output=True, text=True, timeout=15)
                    (out/'SSH收尾.log').write_text(reply.stdout+reply.stderr)
                    require(not Path(self.socket).exists(), '本轮SSH控制连接未退出')
                except Exception as error:
                    errors.append('SSH收尾失败：'+str(error))
        (out/'阶段归档.json').write_text(json.dumps(dict(完整=not errors, 错误=errors, 客机仍存活=alive),
                                                     ensure_ascii=False, indent=2))
        require(not errors and not alive, '采证/归档/关机未通过，停止后续阶段：'+str(errors))

    def arm(self, stage, mode):
        require(stage in ('short', 'long') and mode in ('greedy', 'stable'), '未登记业务阶段')
        tag = stage+'-'+mode
        self.parent_image = old.PARENT
        out = self.output/tag
        out.mkdir()
        remote = '/home/femu/rrzc-sst-results/'+tag
        expected = json.loads((self.output/'同库身份.json').read_text())
        error = None
        try:
            self.start(tag)
            require(digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected))) == expected,
                    '启动前已验证源码、库或程序身份不符')
            self.root(f'nohup setsid bash {self.target}/run_stable_ycsb_guest.sh {tag} {mode} {sizes(stage)[1]} '
                      f'< /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：不限速业务及独立全键校验', 客机结果目录=remote, 计划业务请求数=sizes(stage)[1])
            self.observe(tag)
        except BaseException as cause:
            error = cause
            self.record(原始业务或监督错误=type(cause).__name__+'：'+str(cause))
            raise
        finally:
            try:
                self.finish(out, remote)
            except Exception as cleanup:
                if error is None:
                    raise
                self.record(收尾另有错误=str(cleanup))
        result = analyze_arm(out/tag, stage, mode)
        root = out/tag
        zen = (root/'zenfs.run.log').read_text()
        require((root/'RR调度.txt').read_text().strip() == '等待优先开关=1' and
                'RR公平队列配置：启用=1，每轮最多维护=1，原阈值=1024，未改变检查周期=1' in zen,
                '共享RR公平分支未确认')
        require(zen.count('RR公平队列服务：') == zen.count('RR 开始：'), 'RR排队服务账目不闭合')
        stalls = analyze_stalls((root/'aux/rrzc-ycsb/LOG').read_text(), zen)
        (out/'停顿关联分析.json').write_text(json.dumps(stalls, ensure_ascii=False, indent=2))
        (out/'独立核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        if stage == 'short':
            live = json.loads((out/Path(self.control).name/(tag+'-live')/'采证.json').read_text())
            require(live['完整'] and not live['业务包装已结束'], '未通过业务进行中采证演练，不进入长测')
        self.state['已完成'].append(tag)
        self.record(阶段=tag+'数据/维护/采证验收通过，客机已退出', 最近停顿分析=stalls,
                    最近结果={n: result[n] for n in ('RR次数', 'ZC次数', '总迁移字节', '总成功ZoneReset', '吞吐量')})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default=LABEL)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--validated-short', type=Path, help='只读复核已通过的新采证短对照，避免重复执行短测')
    args = parser.parse_args()
    args.phase, args.scenarios = 'build', []
    base.PARENT, base.PARENT_MANIFEST = old.PARENT, old.MANIFEST
    output, profile = base.preflight(args)
    stages = ('long',) if args.validated_short else ('short', 'long')
    for stage in stages:
        for mode in ('greedy', 'stable'):
            tag = stage+'-'+mode
            for p in (IMAGE_DIR/f'{args.label}-{tag}.qcow2', Path(f'/tmp/{args.label}-{tag}.pid'),
                      Path(f'/tmp/{args.label}-{tag}.qmp'), Path(f'/tmp/{args.label}-{tag}-ssh.sock')):
                require(not p.exists(), '新目标已存在，拒绝覆盖：'+str(p))
    expected, short_pair = old.checked_source()
    if args.validated_short:
        source = args.validated_short.resolve()
        require(source.parent == IMAGE_DIR.resolve() and source.name.endswith('-results'), '短测来源不在授权结果目录')
        short_pair = compare_pair(source, 'short')
        require(json.loads((source/'同库身份.json').read_text()) == expected, '继承短测的软件身份不同')
        for mode in ('greedy', 'stable'):
            tag = 'short-'+mode
            for live in (source/tag).glob('*-evidence/'+tag+'-live/采证.json'):
                report = json.loads(live.read_text())
                require(report['完整'] and not report['业务包装已结束'], '继承的进行中采证演练未通过')
                break
            else:
                raise RuntimeError('缺少已通过的进行中采证演练：'+tag)
    if args.check:
        print('软件身份、原始短测、停止父镜像、新路径和资源检查通过；未启动')
        return
    password = getpass.getpass('修复采证流程客机认证（仅驻留内存）：')
    output.mkdir()
    old.freeze(output, expected, short_pair, profile)
    plan = json.loads((output/'预注册计划.json').read_text())
    plan.update(阶段=['short-greedy', 'short-stable', 'long-greedy', 'long-stable'],
                阶段规模='短对照每组40万请求验证新采证收尾，全部通过后长对照每组400万；装载491520键不变',
                收尾='短对照1500秒，长对照两组统一3600秒；连续180秒业务无进展仍停止；关机前独立采证、哈希复核、正常关机与退出复查，不强杀或自动重跑',
                时限依据='原长测1480秒约267万请求，持续回压下约1804ops/s；400万粗估37分钟，60分钟留波动/校验余量，不保证一定完成',
                诊断='双方同一监督：每10秒进度含维护计数，每60秒只读线程等待采样，短测各演练一次业务进行中采证；结束/失败保存完整可读日志与设备状态；不attach调试器或暂停业务',
                原失败='/home/data/xwz/rrzc-baselines/joint-rr-fair-long-v1-20261021-results')
    if args.validated_short:
        plan.update(阶段=['long-greedy', 'long-stable'], 已通过短测=str(args.validated_short.resolve()),
                    阶段规模='继承并重新核验双方40万请求及真实采证演练，只执行双方400万请求',
                    进展修正='180秒保护只认实际完成请求数或装载阶段切换，不把经过秒数/后台维护当作业务进展')
    (output/'预注册计划.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
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
    signal.signal(signal.SIGTERM, old.interrupted)
    run = CollectedRun(args, password, output, profile)
    run.state['模式顺序'] = [stage+'-'+mode for stage in stages for mode in ('greedy', 'stable')]
    try:
        run.record(阶段='已复核短对照采证，准备长测' if args.validated_short else '新采证监督准备，先短对照验收，再自动进入登记长对照')
        for stage in stages:
            for mode in ('greedy', 'stable'):
                run.arm(stage, mode)
            result = compare_pair(output, stage)
            with (output/(stage+'配对核验.json')).open('x') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
            run.record(阶段=stage+'配对及采证收尾全部通过，客机均已退出', 最近差值=result['模型相对Greedy差值'])
        run.record(阶段='短对照及400万请求配对完成，全部客机退出，等待维护成本及稳态分析')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑', 错误=type(error).__name__+'：'+str(error))
        raise
    finally:
        run.password = None


if __name__ == '__main__':
    main()
