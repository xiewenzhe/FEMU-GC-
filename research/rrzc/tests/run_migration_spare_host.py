#!/usr/bin/env python3
"""隔离编译、真实设备开关定向测试、原规模Greedy验证；失败归档后正常关机。"""
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
from analyze_ycsb_retention import audit
from analyze_ycsb_backpressure import check_options
from analyze_ycsb_overlap import opportunities
from replay_space_emergency import replay as space_replay
from replay_single_recovery import replay as single_replay
from check_ycsb_independent_trace import check

LABEL = 'ycsb-migration-spare-20261019'
# 工作区仅余约1.6GiB，大归档与子镜像统一放在此前获准的data目录。
OUTPUT = IMAGE_DIR / (LABEL + '-results')
PARENT = IMAGE_DIR / 'ycsb-independent-20261018-build.qcow2'
BUILD_IMAGE = IMAGE_DIR / (LABEL + '-build.qcow2')
DIRECTORY = '/home/femu/rrzc-ycsb-retention-20261017-tests'
NAME = 'migration-spare-20261019'
TARGET = DIRECTORY + '/' + NAME
RESULT = '/home/femu/rrzc-ycsb-results/20261017'
TESTS = PROJECT / 'research/rrzc/tests'
FILES = [TESTS / n for n in ('build_migration_spare_guest.sh', 'migration_spare_probe.cc',
    'run_migration_spare_probe_guest.sh', 'run_ycsb_migration_spare_guest.sh',
    'workload-independent-zns.properties', 'probe_ycsb_progress.py')]
FILES += [PROJECT / 'research/rrzc/zenfs-migration-spare.patch']


def preflight(phase):
    parent = PARENT if phase == 'build' else BUILD_IMAGE
    require(parent.is_file() and parent.resolve().parent == IMAGE_DIR and
            not running_parent(parent), '父镜像不符或仍在运行')
    require(Path('/dev/kvm').exists(), 'KVM不可用')
    require(not OUTPUT.exists() if phase == 'build' else (OUTPUT / '同库身份.json').is_file(),
            '阶段输出不符')
    with socket.socket() as probe:
        require(probe.connect_ex(('127.0.0.1', 8096)) != 0, '8096被占用，不操作旧客机')
    modes = {'build': ('build',), 'probe': ('off-v2', 'on-v2'), 'run': ('greedy',)}[phase]
    for mode in modes:
        for path in (IMAGE_DIR / f'{LABEL}-{mode}.qcow2', Path(f'/tmp/{LABEL}-{mode}.pid'),
                     Path(f'/tmp/{LABEL}-{mode}.qmp'), Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not path.exists(), '目标已存在，拒绝覆盖：' + str(path))
    stat = os.statvfs(IMAGE_DIR)
    require(stat.f_bavail * stat.f_frsize >= 16 << 30, 'data盘可用量低于16GiB')
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    require(int(memory['MemAvailable'].split()[0]) >= 16 * 1024 * 1024, '可用内存低于16GiB')


class SpareRun(TargetRun):
    label_prefix = LABEL
    parent_image = PARENT
    port = '8096'

    def collect_target(self, output, stage):
        archive = f'{DIRECTORY}/{NAME}-{stage}.tar.gz'
        self.ssh(f'test ! -e {archive} && tar --exclude="{NAME}/selftest-*/db" '
                 f'--exclude="{NAME}/migration_spare_probe" -czf {archive} -C {DIRECTORY} {NAME}', 120)
        self.command(self.scp() + [f'femu@127.0.0.1:{archive}', str(output)], 120)
        self.command(['tar', '-xzf', str(output / Path(archive).name), '-C', str(output)])

    def stop_archived(self):
        self.shutdown()
        self.record(当前客机PID=None, 正常关机=True)

    def build_target(self):
        plan = dict(目的='修复公共分段迁移接续，不修改victim模型或宣称算法收益',
            改动='默认关闭的当前迁移区加一个独占空替补；仅当前区放不下片段时切换，回收空区优先补替补',
            代价='普通写入少一个256MiB空区；需原规模验证是否导致更早空间压力',
            不变项='设备8GiB，RR4096，GC20%，候选与模型，原始独立读写请求，L0配置2/4/8，无WAL',
            验证顺序=['静态库、绑定和校验器重编及双次普通目录全键校验',
                '关闭复现接续不足，开启验证跨目标数据、Reset、替补独占及普通写入恢复',
                '原装载655360键和120万请求，结束全键校验；无机会不做模型收益结论'],
            限制='定向测试手动维护有效量，不是完整文件映射/RR实验；一个替补不保证任意负载永不耗尽',
            失败处理='独立原始证据归档后仅正常关闭本轮客机，不强杀、不自动重跑')
        (OUTPUT / '预注册计划.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
        sources = OUTPUT / '实验源'
        sources.mkdir()
        self.command(['cp', *map(str, FILES), str(Path(__file__).resolve()), str(sources)])
        self.start('build')
        parent = json.loads((PROJECT / 'research/rrzc/results/ycsb-independent-20261018/同库身份.json').read_text())
        identity = self.ssh('cd /home/femu/rocksdb; sha256sum ' + ' '.join(parent))
        require(hashes(identity) == parent, '构建父镜像身份不符')
        manifest = sources / 'parent.software.sha256'
        manifest.write_text(identity)
        self.ssh(f'test ! -e {TARGET} && mkdir {TARGET}')
        self.command(self.scp() + [*map(str, FILES), str(manifest), f'femu@127.0.0.1:{TARGET}/'])
        output = OUTPUT / 'build'
        output.mkdir()
        try:
            self.ssh(f'nohup setsid bash {TARGET}/build_migration_spare_guest.sh < /dev/null > {TARGET}/build-launcher.log 2>&1 &')
            self.record(阶段='编译公共空替补、重链YCSB并进行双次普通目录回归')
            self.wait_for(f'{TARGET}/build.finished',
                f'test ! -f {TARGET}/library-build.log || tail -1 {TARGET}/library-build.log', 1800)
        finally:
            self.collect_target(output, 'build')
        built = output / NAME
        require((built / 'build.exit').read_text().strip() == '0' and
                (built / 'selftest.exit').read_text().strip() == '0', '编译或普通目录校验未通过')
        software = hashes((built / 'software.sha256').read_text())
        changed = {name for name, digest in parent.items() if software.get(name) != digest}
        require(changed == {'plugin/zenfs/fs/zbd_zenfs.cc', 'plugin/zenfs/fs/zbd_zenfs.h',
                'librocksdb_debug.a', DIRECTORY + '/ycsb/ycsb', DIRECTORY + '/verify_ycsb'},
                '软件变化超出公共资源补丁及重链消费者')
        for name in ('zbd_zenfs.cc', 'zbd_zenfs.h'):
            frozen = PROJECT / 'build-femu/rrzc/migration-spare-work/fs' / name
            require(software['plugin/zenfs/fs/' + name] == hashlib.sha256(frozen.read_bytes()).hexdigest(),
                    '实际资源实现与冻结副本不一致')
        for arm in ('first', 'second'):
            require('键数=1024' in (built / f'selftest-{arm}/verify.log').read_text(), '普通目录全键校验缺失')
        (OUTPUT / '同库身份.json').write_text(json.dumps(software, ensure_ascii=False, indent=2))
        self.stop_archived()
        self.record(阶段='构建与双次普通目录校验通过，尚未运行设备定向测试')

    def probes(self):
        self.parent_image = BUILD_IMAGE
        software = json.loads((OUTPUT / '同库身份.json').read_text())
        query = 'cd /home/femu/rocksdb; sha256sum ' + ' '.join(software)
        results = {}
        # 首次off在打开设备前被调度器检查拒绝；保留原结果，新子镜像只补齐业务已有前置条件。
        snapshot = OUTPUT / '定向入口补齐'
        snapshot.mkdir()
        self.command(['cp', str(Path(__file__).resolve()), str(TESTS / 'test_migration_spare.py'), str(snapshot)])
        for mode in ('off', 'on'):
            self.start(mode + '-v2')
            output = OUTPUT / (mode + '-v2')
            output.mkdir()
            require(hashes(self.ssh(query)) == software, '定向测试前软件身份不同')
            try:
                self.root('test "$(blockdev --getsize64 /dev/nvme0n1)" = 8589934592 && '
                          'printf "mq-deadline\\n" > /sys/block/nvme0n1/queue/scheduler')
                self.root(f'nohup setsid bash {TARGET}/run_migration_spare_probe_guest.sh {mode} < /dev/null > {TARGET}/probe-{mode}.launcher.log 2>&1 &')
                self.record(阶段=f'{mode}：全空真实FEMU上的分段接续与数据核验')
                self.wait_for(f'{TARGET}/probe-{mode}.finished',
                              f'test ! -f {TARGET}/probe-{mode}.log || tail -1 {TARGET}/probe-{mode}.log', 180)
            finally:
                self.collect_target(output, mode + '-v2')
            built = output / NAME
            require((built / f'probe-{mode}.exit').read_text().strip() == '0', '资源定向测试未通过')
            results[mode] = json.loads((built / f'probe-{mode}.log').read_text().splitlines()[-1])
            require(hashes(self.ssh(query)) == software, '定向测试中软件变化')
            self.stop_archived()
        (OUTPUT / '定向测试核验.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
        self.record(阶段='开关定向测试通过；原规模业务尚未开始', 定向结果=results)

    def business(self):
        require((OUTPUT / '定向测试核验.json').is_file(), '必须先通过真实设备定向测试')
        self.parent_image = BUILD_IMAGE
        software = json.loads((OUTPUT / '同库身份.json').read_text())
        self.start('greedy')
        output = OUTPUT / 'greedy'
        output.mkdir()
        query = 'cd /home/femu/rocksdb; sha256sum ' + ' '.join(software)
        before = self.ssh(query)
        require(hashes(before) == software, '业务前软件身份不同')
        (output / '软件.before.sha256').write_text(before)
        try:
            self.root(f'nohup setsid bash {TARGET}/run_ycsb_migration_spare_guest.sh greedy < /dev/null > {TARGET}/launcher.log 2>&1 &')
            start = changed = time.monotonic()
            last = None
            while time.monotonic() - start < 3000:
                point = json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
                self.record(阶段='空替补公共修复：原规模120万次Greedy及全键核验', 最近观测=point)
                with (OUTPUT / '进度.jsonl').open('a') as stream:
                    stream.write(json.dumps(point, ensure_ascii=False) + '\n')
                require(not point['错误'], '发现后台错误，采证后正常关机，不自动重跑')
                if self.ssh(f'test ! -f {TARGET}/run.finished || printf "完成"').strip() == '完成':
                    break
                key = (point['阶段'], point['操作数'])
                if key != last:
                    last, changed = key, time.monotonic()
                require(time.monotonic() - changed < 180, '连续180秒无进展，采证后正常关机')
                time.sleep(15)
            else:
                raise RuntimeError('原规模验证超过3000秒')
        finally:
            point = json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
            self.collect(output, RESULT)
            self.collect_target(output, 'run')
            after = self.ssh(query)
            (output / '软件.after.sha256').write_text(after)
            log = point.get('维护日志路径')
            require(log and Path(log).parent == Path('/tmp') and Path(log).name.startswith('zenfs_nvme0n1_'),
                    '没有唯一维护日志，不猜测归档')
            self.command(self.scp() + [f'femu@127.0.0.1:{log}', str(output / '现场维护.log')], 120)
            self.stop_archived()
        require(before == after, '业务期间软件变化')
        root = output / '20261017'
        result = audit(root, 'greedy', nowal=True, expected_ops=1200000, single_recovery=True, space_emergency=True)
        check_options('\n'.join(p.read_text(errors='replace') for p in (root / 'aux').rglob('LOG*') if p.is_file()))
        result['实际请求分布'] = check(root / 'audit.requests', 655360, 4096, 1200000)
        lines, _ = space_replay((root / 'zenfs.run.log').read_text().splitlines())
        lines, _ = single_replay(lines)
        result['正常ZC机会'] = opportunities(lines)
        result['实验定位'] = '公共资源修复的原规模可完成性验证，不是victim模型收益对照'
        (output / '独立核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        require(result['验收通过'], '业务正确性或维护账目未通过')
        self.record(阶段='原规模Greedy、全键校验及维护账目通过，客机正常关闭',
                    RR重置=result['RR重置'], ZC重置=result['ZC重置'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('build', 'probe', 'run'), required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    preflight(args.phase)
    if args.check:
        print('停止父镜像、8096端口、内存和data盘空间检查通过，未启动')
        return
    password = getpass.getpass('本轮隔离客机认证（仅驻留内存）：')
    if args.phase == 'build':
        OUTPUT.mkdir()
    run = SpareRun(SimpleNamespace(label=LABEL, seconds=1200, space_weight=50), password, OUTPUT)
    run.state.update(模式顺序=['build', 'off', 'on', 'greedy'], 业务请求数=1200000,
                     观察上限秒数=3000, 公共空替补默认关闭=True)
    try:
        {'build': run.build_target, 'probe': run.probes, 'run': run.business}[args.phase]()
    except BaseException as error:
        run.record(阶段='本阶段失败，不自动重跑；原始归档和镜像保留', 错误=str(error) or type(error).__name__)
        # 只在证据已取回时关闭本轮精确客机；归档失败则保留现场。
        mode = Path(run.state.get('当前镜像') or '').stem.removeprefix(LABEL + '-')
        if run.state.get('当前客机PID') and (OUTPUT / mode / NAME).is_dir():
            run.stop_archived()
        raise


if __name__ == '__main__':
    main()
