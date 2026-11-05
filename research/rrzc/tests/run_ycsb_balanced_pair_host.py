#!/usr/bin/env python3
"""同父镜像串行Greedy与留存净收益配对；不改机制，不覆盖先导。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from run_ycsb_read_pilot_host import TargetRun
from run_tail_ablation_fixed_pair import running_parent
from run_stream_pair import PROJECT, IMAGE_DIR, require
from run_rocksdb_guard_pair_host import hashes
from run_tail_ablation_smoke_pair import DEVICE
from analyze_ycsb_retention import audit, independent_profile, sha
from analyze_ycsb_backpressure import check_options
from analyze_ycsb_balanced_pair import compare, MODES
from check_ycsb_independent_trace import check

LABEL = 'ycsb-balanced-pair-20261019'
OUTPUT = IMAGE_DIR / (LABEL + '-results')
PARENT = IMAGE_DIR / 'ycsb-migration-spare-20261019-build.qcow2'
PILOT = IMAGE_DIR / 'ycsb-balanced-20261019-results'
IDENTITY = IMAGE_DIR / 'ycsb-migration-spare-20261019-results/同库身份.json'
DIRECTORY = '/home/femu/rrzc-ycsb-retention-20261017-tests'
NAME = 'balanced-pair-20261019'
TARGET = DIRECTORY + '/' + NAME
RESULT = '/home/femu/rrzc-ycsb-results/20261017'
TESTS = PROJECT / 'research/rrzc/tests'
FILES = [TESTS / name for name in ('run_ycsb_balanced_pair_guest.sh',
    'check_ycsb_balanced_pair_guest.sh', 'workload-balanced-zns.properties',
    'probe_ycsb_progress.py', 'check_ycsb_independent_trace.py')]


def preflight():
    require(not OUTPUT.exists(), '结果已存在，拒绝覆盖')
    require(PARENT.is_file() and PARENT.resolve().parent == IMAGE_DIR and
            not running_parent(PARENT) and Path('/dev/kvm').exists(), '父镜像或KVM状态不符')
    pilot = json.loads((PILOT / 'greedy/独立核验.json').read_text())
    require(pilot['验收通过'] and pilot['具备进入机制配对条件'], '先导未通过或尚无配对机会')
    properties = dict(s.split('=', 1) for s in FILES[2].read_text().splitlines() if '=' in s and not s.startswith('#'))
    require(independent_profile(properties, 1200000, '50/50'), '不是登记负载')
    require(FILES[2].read_bytes() == (PILOT / 'greedy/20261017/workload.properties').read_bytes(), '负载与先导不同')
    require(sha(PROJECT / 'build-femu/qemu-system-x86_64') ==
            (PILOT / '实验源/FEMU.sha256').read_text().split()[0], 'FEMU与先导不同')
    with socket.socket() as probe:
        require(probe.connect_ex(('127.0.0.1', 8096)) != 0, '8096端口被占用')
    for mode in MODES:
        for path in (IMAGE_DIR / f'{LABEL}-{mode}.qcow2', Path(f'/tmp/{LABEL}-{mode}.pid'),
                     Path(f'/tmp/{LABEL}-{mode}.qmp'), Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not path.exists(), '目标已存在：' + str(path))
    stat = os.statvfs(IMAGE_DIR)
    require(stat.f_bavail * stat.f_frsize >= 24 << 30, 'data盘可用空间不足24GiB')
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    require(int(memory['MemAvailable'].split()[0]) >= 16 * 1024 * 1024, '可用内存不足16GiB')


class BalancedPair(TargetRun):
    label_prefix = LABEL
    parent_image = PARENT
    port = '8096'

    def collect_target(self, output, stage):
        archive = f'{DIRECTORY}/{NAME}-{stage}.tar.gz'
        self.ssh(f'test ! -e {archive} && tar --exclude="{NAME}/selftest-*/db" '
                 f'-czf {archive} -C {DIRECTORY} {NAME}', 120)
        self.command(self.scp() + [f'femu@127.0.0.1:{archive}', str(output)], 120)
        self.command(['tar', '-xzf', str(output / Path(archive).name), '-C', str(output)])

    def arm(self, mode, source, expected):
        require(mode in MODES, '未知模式')
        output = OUTPUT / mode
        output.mkdir()
        self.record(当前模式=mode, 已归档并正常关机=False)
        self.start(mode)
        query = 'cd /home/femu/rocksdb; sha256sum ' + ' '.join(expected)
        identity = self.ssh(query)
        require(hashes(identity) == expected, '父镜像软件与先导不符')
        self.ssh(f'test ! -e {TARGET} && test ! -e {RESULT} && mkdir {TARGET}')
        manifest = source / 'software.sha256'
        content = identity + ''.join(hashlib.sha256(path.read_bytes()).hexdigest() +
            '  ' + TARGET + '/' + path.name + '\n' for path in FILES)
        if manifest.exists():
            require(manifest.read_text() == content, '两组软件或入口不同')
        else:
            manifest.write_text(content)
        self.command(self.scp() + [*map(str, FILES), str(manifest), f'femu@127.0.0.1:{TARGET}/'])
        (output / '软件.before.sha256').write_text(identity)
        small = output / 'selftest'
        small.mkdir()
        try:
            self.ssh(f'nohup setsid bash {TARGET}/check_ycsb_balanced_pair_guest.sh < /dev/null > {TARGET}/selftest-launcher.log 2>&1 &')
            self.record(阶段=f'{mode}同库普通目录双次请求及全键校验')
            try:
                self.wait_for(f'{TARGET}/selftest.finished',
                    f'test ! -f {TARGET}/selftest.exit || cat {TARGET}/selftest.exit', 300)
            finally:
                self.collect_target(small, 'selftest')
            built = small / NAME
            require((built / 'selftest.exit').read_text().strip() == '0', '普通目录测试失败')
            for arm in ('first', 'second'):
                require('键数=1024' in (built / f'selftest-{arm}/verify.log').read_text(), '缺全键校验')
                trace = json.loads((built / f'selftest-{arm}/trace.json').read_text())
                require(trace['读取数'] == 4077 and trace['更新数'] == 4115, '小测试轨迹与先导不符')
            require(self.ssh(query) == identity, '小测试改变了软件')
        except BaseException:
            # 小测试归档成功才能关机；归档失败保留现场。
            if (small / NAME).is_dir():
                self.shutdown()
                self.record(当前客机PID=None, 已归档并正常关机=True)
            raise
        try:
            self.root(f'nohup setsid bash {TARGET}/run_ycsb_balanced_pair_guest.sh {mode} < /dev/null > {TARGET}/launcher.log 2>&1 &')
            self.record(阶段=f'{mode}：真实ZNS装载及120万次读写各半业务', 小测试请求=trace)
            start = changed = time.monotonic()
            last = None
            while time.monotonic() - start < 3000:
                point = json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
                self.record(阶段=f'{mode}：120万次业务及全键校验', 最近观测=point)
                with (output / '进度.jsonl').open('a') as stream:
                    stream.write(json.dumps(point, ensure_ascii=False) + '\n')
                require(not point['错误'], '后台错误，归档后正常关闭，不重跑')
                if self.ssh(f'test ! -f {TARGET}/run.finished || printf "完成"').strip() == '完成':
                    break
                key = (point['阶段'], point['操作数'])
                if key != last:
                    last, changed = key, time.monotonic()
                require(time.monotonic() - changed < 180, '连续180秒无进展')
                time.sleep(15)
            else:
                raise RuntimeError('超过3000秒观察上限')
        finally:
            self.collect_target(output, 'run')
            if self.ssh(f'test ! -d {RESULT} || printf "存在"').strip() == '存在':
                self.collect(output, RESULT)
            after = self.ssh(query)
            (output / '软件.after.sha256').write_text(after)
            point = json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
            log = point.get('维护日志路径')
            if log:
                require(Path(log).parent == Path('/tmp') and Path(log).name.startswith('zenfs_nvme0n1_'), '维护日志路径不符')
                self.command(self.scp() + [f'femu@127.0.0.1:{log}', str(output / '现场维护.log')], 120)
            self.shutdown()
            self.record(当前客机PID=None, 已归档并正常关机=True)
        require(identity == after, '业务期间软件变化')
        require((output / NAME / 'run.exit').read_text().strip() == '0', '包装器未成功')
        root = output / '20261017'
        result = audit(root, mode, nowal=True, expected_ops=1200000,
            single_recovery=True, space_emergency=True, independent_ratio='50/50')
        check_options('\n'.join(p.read_text(errors='replace') for p in (root / 'aux').rglob('LOG*') if p.is_file()))
        result['实际请求分布'] = check(root / 'audit.requests', 655360, 4096, 1200000)
        (output / '独立核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        require(result['验收通过'], '数据或维护验收未通过，停止后续组')
        self.state['已完成'].append(mode)
        self.record(阶段=f'{mode}完整归档核验通过并正常关机', RR重置=result['RR重置'],
                    ZC重置=result['ZC重置'], 实际改选次数=result['实际改选次数'])

    def run_pair(self):
        plan = dict(目的='验证当前留存净收益victim selection相对Greedy的实际效果，不预设正收益',
            模式顺序=list(MODES), 父镜像=str(PARENT), 设备参数=DEVICE,
            唯一处理差异='Greedy为guarded=shadow/retention=observe；模型为guarded=execute/retention=execute；cost_rank均为0',
            不变项='同库同配置；8GiB、RR4096、GC20%、正常192MiB回收资格、10秒紧迫界限、16MiB额外预算、公共恢复及空替补、无WAL、L0配置2/4/8',
            负载='YCSB-C++派生独立选键，不是标准Workload A；655360个4KiB键、120万请求、50%热读50%全空间均匀更新、4096热键、1000次/秒、单线程种子20261017',
            正确性='每组普通目录双次小回归；全空ZNS装载、在线读值、全键最新值、同请求轨迹、同软件、维护账目及逐轮决策核验',
            主指标='业务期总迁移、RR/ZC重置、实际改选、读与Update调用均值和p95/p99；同时列全程和公共恢复次数',
            边界='只做一次串行配对，不声称稳态或显著性；若无实际改选，不把组间波动解释为模型收益',
            先导边界='4轮短期影子机会涉及2个Zone代际，不等于4次可避免RR或4次留存模型改选',
            失败处理='每组3000秒上限、180秒无进展或后台错误则采证，归档成功后正常关闭本轮客机并停止后续组；不自动重跑')
        (OUTPUT / '预注册计划.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
        source = OUTPUT / '实验源'
        source.mkdir()
        self.command(['cp', *map(str, FILES), str(Path(__file__).resolve()), str(source)])
        for folder, paths in (('分析入口', (PROJECT / 'research/rrzc').glob('*.py')),
                              ('宿主入口', TESTS.glob('*.py'))):
            directory = source / folder
            directory.mkdir()
            self.command(['cp', *map(str, paths), str(directory)])
        (source / 'FEMU.sha256').write_text(self.command(['sha256sum', 'build-femu/qemu-system-x86_64']))
        expected = json.loads(IDENTITY.read_text())
        (OUTPUT / '同库身份.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2))
        for mode in MODES:
            self.arm(mode, source, expected)
        comparison = compare(OUTPUT)
        (OUTPUT / '两组对照.json').write_text(json.dumps(comparison, ensure_ascii=False, indent=2))
        self.password = None
        self.record(阶段='两组完整完成、配对核验通过，客机均已正常关闭', 当前客机PID=None,
                    配对核验通过=True, 实际模型改选=comparison['各组摘要']['retention']['实际改选次数'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    preflight()
    if args.check:
        print('停止父镜像、先导证据、同负载、端口和资源检查通过，未启动')
        return
    password = getpass.getpass('配对隔离客机认证（仅内存）：')
    OUTPUT.mkdir()
    if args.detach:
        pid = os.fork()
        if pid:
            print(f'监督进程PID={pid}，状态={OUTPUT / "状态.json"}', flush=True)
            return
        os.setsid()
        log = os.open(OUTPUT / '监督进程.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.dup2(log, 1)
        os.dup2(log, 2)
        os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    run = BalancedPair(SimpleNamespace(label=LABEL, seconds=1200, space_weight=50), password, OUTPUT)
    run.state.update(模式顺序=list(MODES), 业务请求数=1200000, 观察上限秒数=3000, 业务读写比例='50/50')
    try:
        run.run_pair()
    except BaseException as error:
        run.record(阶段='本轮失败并停止后续组，不自动重跑；客机状态见PID和归档字段',
                   错误=str(error) or type(error).__name__)
        raise


if __name__ == '__main__':
    main()
