#!/usr/bin/env python3
"""同库单变量50%读/50%更新先导；不改模型，不自动进入算法对照。"""
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
from analyze_ycsb_retention import audit, independent_profile
from analyze_ycsb_backpressure import check_options
from analyze_ycsb_overlap import opportunities
from replay_space_emergency import replay as space_replay
from replay_single_recovery import replay as single_replay
from check_ycsb_independent_trace import check

LABEL = 'ycsb-balanced-20261019'
OUTPUT = IMAGE_DIR / (LABEL + '-results')
PARENT = IMAGE_DIR / 'ycsb-migration-spare-20261019-build.qcow2'
PREVIOUS = IMAGE_DIR / 'ycsb-migration-spare-20261019-results'
DIRECTORY = '/home/femu/rrzc-ycsb-retention-20261017-tests'
NAME = 'balanced-20261019'
TARGET = DIRECTORY + '/' + NAME
RESULT = '/home/femu/rrzc-ycsb-results/20261017'
TESTS = PROJECT / 'research/rrzc/tests'
FILES = [TESTS / name for name in ('run_ycsb_balanced_guest.sh', 'check_ycsb_balanced_guest.sh',
    'workload-balanced-zns.properties', 'probe_ycsb_progress.py', 'check_ycsb_independent_trace.py')]


def properties(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if '=' in line and not line.startswith('#'))


def preflight():
    require(not OUTPUT.exists(), '结果已存在，拒绝覆盖')
    require(PARENT.is_file() and PARENT.resolve().parent == IMAGE_DIR and
            not running_parent(PARENT) and Path('/dev/kvm').exists(), '父镜像或KVM状态不符')
    require(json.loads((PREVIOUS / 'greedy/独立核验.json').read_text())['验收通过'], '前一轮公共修复未通过')
    old = properties(TESTS / 'workload-independent-zns.properties')
    new = properties(TESTS / 'workload-balanced-zns.properties')
    require({key for key in old.keys() | new.keys() if old.get(key) != new.get(key)} ==
            {'readproportion', 'updateproportion'}, '配置变化超过读写比例')
    require(independent_profile(new, 1200000, '50/50'), '不是本轮登记配置')
    with socket.socket() as probe:
        require(probe.connect_ex(('127.0.0.1', 8096)) != 0, '8096端口占用')
    for path in (IMAGE_DIR / (LABEL + '-greedy.qcow2'), Path(f'/tmp/{LABEL}-greedy.pid'),
                 Path(f'/tmp/{LABEL}-greedy.qmp'), Path(f'/tmp/{LABEL}-greedy-ssh.sock')):
        require(not path.exists(), '新目标已存在：' + str(path))
    stat = os.statvfs(IMAGE_DIR)
    require(stat.f_bavail * stat.f_frsize >= 12 << 30, 'data盘空间不足12GiB')
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    require(int(memory['MemAvailable'].split()[0]) >= 16 * 1024 * 1024, '可用内存不足16GiB')


class BalancedRun(TargetRun):
    label_prefix = LABEL
    parent_image = PARENT
    port = '8096'

    def collect_target(self, output, stage):
        archive = f'{DIRECTORY}/{NAME}-{stage}.tar.gz'
        self.ssh(f'test ! -e {archive} && tar --exclude="{NAME}/selftest-*/db" '
                 f'-czf {archive} -C {DIRECTORY} {NAME}', 120)
        self.command(self.scp() + [f'femu@127.0.0.1:{archive}', str(output)], 120)
        self.command(['tar', '-xzf', str(output / Path(archive).name), '-C', str(output)])

    def run_target(self):
        plan = dict(目的='检查增加更新相对强度后，空间资格与近期RR风险是否产生交集',
            唯一负载变化='80%读/20%更新改为50%读/50%更新，不是模型权重变化',
            模式='实际Greedy，模型只观察；YCSB-C++派生独立选键负载，不是标准Workload A',
            不变项='同一FEMU、ZenFS、RocksDB、YCSB二进制；8GiB、RR4096、GC20%、192MiB回收资格、10秒紧迫界限、16MiB额外预算、空替补、无WAL、L0配置2/4/8',
            规模='装载655360个4KiB键；120万请求；4096热读键Zipfian0.99、全空间均匀更新；1000次/秒速率上限，单线程种子20261017',
            最小验证='同一二进制普通目录两次1024键/8192请求、完整最新值及请求一致性，随后全空真实ZNS业务',
            正式验证='在线读值、业务完整计数、结束655360键最新值、维护复制账目、实际Options、逐轮候选',
            主要判据='业务RR和ZC均出现，且存在非Greedy短期合格备选与预算内影子建议；不把单纯次数变化当作模型收益',
            风险='压实也可能提前搬走热数据而降低RR，不保证形成机会或净收益',
            时间边界='装载、交界一秒与业务分开；不满足无装载维护时不将全日志机会算作业务机会',
            失败处理='3000秒总上限或180秒无进展/后台错误后采证，归档成功才正常关闭本轮客机；不自动重跑')
        (OUTPUT / '预注册计划.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
        source = OUTPUT / '实验源'
        source.mkdir()
        self.command(['cp', *map(str, FILES), str(Path(__file__).resolve()), str(source)])
        analysis = source / '分析入口'
        analysis.mkdir()
        self.command(['cp', *map(str, (PROJECT / 'research/rrzc').glob('*.py')), str(analysis)])
        (source / 'FEMU.sha256').write_text(self.command(['sha256sum', 'build-femu/qemu-system-x86_64']))
        self.start('greedy')
        parent = json.loads((PREVIOUS / '同库身份.json').read_text())
        query = 'cd /home/femu/rocksdb; sha256sum ' + ' '.join(parent)
        identity = self.ssh(query)
        require(hashes(identity) == parent, '与上一轮同库身份不同')
        self.ssh(f'test ! -e {TARGET} && test ! -e {RESULT} && mkdir {TARGET}')
        manifest = source / 'software.sha256'
        manifest.write_text(identity + ''.join(hashlib.sha256(path.read_bytes()).hexdigest() +
            '  ' + TARGET + '/' + path.name + '\n' for path in FILES))
        self.command(self.scp() + [*map(str, FILES), str(manifest), f'femu@127.0.0.1:{TARGET}/'])
        (OUTPUT / '同库身份.json').write_text(json.dumps(hashes(manifest.read_text()), ensure_ascii=False, indent=2))
        small = OUTPUT / 'selftest'
        small.mkdir()
        try:
            self.ssh(f'nohup setsid bash {TARGET}/check_ycsb_balanced_guest.sh < /dev/null > {TARGET}/selftest-launcher.log 2>&1 &')
            self.record(阶段='同库50/50请求比例：普通目录双次全键校验')
            self.wait_for(f'{TARGET}/selftest.finished', f'test ! -f {TARGET}/selftest.exit || cat {TARGET}/selftest.exit', 300)
        finally:
            self.collect_target(small, 'selftest')
        built = small / NAME
        require((built / 'selftest.exit').read_text().strip() == '0', '普通目录测试未通过')
        for arm in ('first', 'second'):
            require('键数=1024' in (built / f'selftest-{arm}/verify.log').read_text(), '全键校验缺失')
            trace = json.loads((built / f'selftest-{arm}/trace.json').read_text())
            require(3800 <= trace['读取数'] <= 4400 and trace['读取数'] + trace['更新数'] == 8192,
                    '小测试实际请求比例不符')
        require(self.ssh(query) == identity, '小测试修改了软件')
        self.record(阶段='普通目录双次校验通过，准备原规模真实ZNS业务', 小测试请求=trace)
        output = OUTPUT / 'greedy'
        output.mkdir()
        (output / '软件.before.sha256').write_text(identity)
        try:
            self.root(f'nohup setsid bash {TARGET}/run_ycsb_balanced_guest.sh greedy < /dev/null > {TARGET}/launcher.log 2>&1 &')
            start = changed = time.monotonic()
            last = None
            while time.monotonic() - start < 3000:
                point = json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
                self.record(阶段='50%读/50%更新：120万次Greedy业务及全键校验', 最近观测=point)
                with (OUTPUT / '进度.jsonl').open('a') as stream:
                    stream.write(json.dumps(point, ensure_ascii=False) + '\n')
                require(not point['错误'], '后台错误，采证后正常关机，不重跑')
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
            # 初始化失败可能没有业务目录或维护日志；已存在的证据逐项保存。
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
        root = output / '20261017'
        result = audit(root, 'greedy', nowal=True, expected_ops=1200000,
            single_recovery=True, space_emergency=True, independent_ratio='50/50')
        check_options('\n'.join(p.read_text(errors='replace') for p in (root / 'aux').rglob('LOG*') if p.is_file()))
        result['实际请求分布'] = check(root / 'audit.requests', 655360, 4096, 1200000)
        lines, _ = space_replay((root / 'zenfs.run.log').read_text().splitlines())
        lines, _ = single_replay(lines)
        result['正常ZC机会'] = opportunities(lines)
        phases = result['分阶段维护']
        no_load = all(phases[p][k] == 0 for p in ('装载及等待', '交界一秒不确定') for k in ('ZC尝试', 'RR尝试'))
        result['具备进入机制配对条件'] = (result['验收通过'] and no_load and
            phases['业务']['RR重置'] > 0 and phases['业务']['ZC重置'] > 0 and
            result['正常ZC机会']['包含非Greedy临RR候选轮数'] > 0 and result['正常ZC机会']['影子会改选轮数'] > 0)
        result['实验定位'] = '同库改变读写比例的候选交集先导，不是victim模型收益或标准Workload A'
        (output / '独立核验.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        require(result['验收通过'], '数据或维护验收未通过')
        self.record(阶段='50/50负载完整完成并核验，客机已正常关闭', RR重置=result['RR重置'],
                    ZC重置=result['ZC重置'], 具备进入机制配对条件=result['具备进入机制配对条件'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    preflight()
    if args.check:
        print('同库父镜像、单变量配置、8096端口和资源检查通过，未启动')
        return
    password = getpass.getpass('本轮隔离客机认证（只驻留内存）：')
    OUTPUT.mkdir()
    run = BalancedRun(SimpleNamespace(label=LABEL, seconds=1200, space_weight=50), password, OUTPUT)
    run.state.update(模式顺序=['greedy'], 业务请求数=1200000, 观察上限秒数=3000, 业务读写比例='50/50')
    try:
        run.run_target()
    except BaseException as error:
        run.record(阶段='本轮失败，保留证据；不自动重跑', 错误=str(error) or type(error).__name__)
        # 小测试失败且已归档时，也只正常关闭本轮客机，不遗留后台业务。
        if run.state.get('当前客机PID') and not (OUTPUT / 'greedy').exists() and (OUTPUT / 'selftest' / NAME).is_dir():
            run.shutdown()
            run.record(当前客机PID=None, 已归档并正常关机=True)
        raise


if __name__ == '__main__':
    main()
