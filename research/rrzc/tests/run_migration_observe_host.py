#!/usr/bin/env python3
"""新子镜像完整构建后单次Greedy观测；失败保留现场，不改旧实验或自动重跑。"""
import argparse,getpass,hashlib,json,os,socket
from pathlib import Path
from types import SimpleNamespace
from run_rocksdb_anchor_slow_host import SlowRun
from run_rocksdb_guard_pair_host import hashes
from run_cost_rank_host import running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from analyze_rocksdb_observe import analyze

LABEL='rocksdb-migration-observe-20261017'
DIRECTORY=f'/home/femu/rrzc-{LABEL}-tests'
RESULT='/home/femu/rrzc-stream-results/rocksdb-anchor-slow-20261016'
OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'rocksdb-guard-pair-20261016-build.qcow2'
TESTS=PROJECT/'research/rrzc/tests'
PORT='8092'
NAMES=['rocksdb_observe_test.cc','rocksdb_observe_plan.h','test_rocksdb_observe_plan.cc',
       'test_rocksdb_anchor_plan.cc','test_migration_window.cc','rocksdb_observe_migration.patch',
       'build_migration_observe_guest.sh','run_migration_observe_guest.sh']

def base_identity():
    return {n:d for n,d in json.loads((PROJECT/'research/rrzc/results/rocksdb-guard-pair-20261016/同库身份.json').read_text()).items() if not n.startswith('/')}

def preflight():
    require(not OUTPUT.exists() and PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR and not running_parent(PARENT),'父镜像或输出不符')
    require(Path('/dev/kvm').exists(),'宿主KVM不可用')
    with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,'8092占用，保留已有客机')
    for p in (IMAGE_DIR/f'{LABEL}-pilot.qcow2',Path(f'/tmp/{LABEL}-pilot.pid'),Path(f'/tmp/{LABEL}-pilot.qmp'),Path(f'/tmp/{LABEL}-pilot-ssh.sock')):
        require(not p.exists(),'新目标已存在：'+str(p))
    for p,minimum in ((IMAGE_DIR,8<<30),(PROJECT,2<<30)):
        s=os.statvfs(p);require(s.f_bavail*s.f_frsize>=minimum,'空间不足')
    previous=PROJECT/'research/rrzc/results/rocksdb-guard-pair-20261016/实验源'
    for name in NAMES[:4]:require((TESTS/name).read_bytes()==(previous/name).read_bytes(),'原固定请求源已变化')

class MigrationObserve(SlowRun):
    label_prefix=LABEL
    parent_image=PARENT
    directory=DIRECTORY
    result=RESULT
    port=PORT
    def run(self):
        self.state.pop('实验空间权重百分比');self.state['模式顺序']=['新子镜像编译','同一新客机单次Greedy观察']
        plan=dict(写阶段秒数=180,写速率键每秒=4096,覆盖写键数=737280,读阶段秒数=120,读次数=48000,
            初始键数=655360,值字节=4096,RR阈值=4096,GC启动比例=20,GC斜率=3,
            回收资格MiB=192,紧迫界限秒=10,额外预算MiB=16,真实策略='原Greedy，不执行任何新建议',
            观测开关='ZENFS_RRZC_MIGRATION_WINDOW=1',双点影子=False,成本排序=False,
            负载='原anchors180slow，仅观察程序增加独立migration.csv，业务请求和原CSV不变',
            验收='完整编译与POSIX回归、全量最新值、软件身份、迁移标记与真实候选历史；覆盖不足仍保留结果',
            结论边界='单次信号采集，不是新机制收益或稳态，不与旧库性能作因果对照',
            自动重跑=False,构建超时秒=1800,业务观察超时秒=900,失败处理='先归档，保留客机与业务，不杀进程或重跑')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        frozen=OUTPUT/'实验源';frozen.mkdir();(frozen/'分析').mkdir()
        sources=[TESTS/n for n in NAMES]+[PROJECT/'research/rrzc/model/rrzc_migration_window.h',PROJECT/'research/rrzc/zenfs-migration-window.patch']
        self.command(['cp',*map(str,sources),str(Path(__file__).resolve()),str(frozen)])
        self.command(['cp',*map(str,(PROJECT/'research/rrzc').glob('*.py')),str(frozen/'分析')])
        (OUTPUT/'宿主FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        self.record(阶段='单次迁移窗口观察已登记，不修改选区规则',固定计划=plan)
        self.start('pilot');core=base_identity();query='cd /home/femu/rocksdb; sha256sum '+' '.join(core)
        before=self.ssh(query);require(hashes(before)==core,'父镜像核心身份与既有完整实验不符')
        (OUTPUT/'生产身份.before.sha256').write_text(before)
        base=OUTPUT/'source.base.sha256';base.write_text(before)
        self.ssh(f'test ! -e {DIRECTORY} && test ! -e {RESULT} && test ! -e {RESULT}-aux && mkdir {DIRECTORY}')
        self.command(self.scp()+[*map(str,sources),str(base),f'femu@127.0.0.1:{DIRECTORY}/'])
        build=OUTPUT/'build';build.mkdir()
        try:
            self.ssh(f'nohup setsid bash {DIRECTORY}/build_migration_observe_guest.sh < /dev/null > {DIRECTORY}/build-launcher.log 2>&1 &')
            self.record(阶段='新客机完整重编库与测试程序，随后普通文件系统回归')
            self.observe_until(f'{DIRECTORY}/build.finished',f'test ! -f {DIRECTORY}/library-build.log || tail -1 {DIRECTORY}/library-build.log',1800)
        finally:errors=self.collect(build,[DIRECTORY])
        require(not errors,'构建归档失败，保留客机')
        archive=build/Path(DIRECTORY).name
        require((archive/'build.exit').read_text().strip()=='0' and (archive/'selftest.exit').read_text().strip()=='0','编译或最小回归失败，原始日志已归档')
        expected=hashes((archive/'source.after.sha256').read_text())
        changed=sorted(n for n in core if expected.get(n)!=core[n])
        require(changed==['librocksdb_debug.a','plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/fs_zenfs.h'],'核心修改超出观测接线及重编库')
        for name in ('fs_zenfs.cc','fs_zenfs.h'):
            require(expected[f'plugin/zenfs/fs/{name}']==hashlib.sha256((PROJECT/'build-femu/rrzc/migration-window-work/fs'/name).read_bytes()).hexdigest(),'客机接线源与核验副本不同')
        require(expected['plugin/zenfs/fs/rrzc_migration_window.h']==hashlib.sha256(sources[-2].read_bytes()).hexdigest(),'迁移统计头身份不符')
        testnames=['rocksdb_observe_test','rocksdb_observe_test.cc','rocksdb_observe_plan.h','test_rocksdb_observe_plan.cc','test_rocksdb_anchor_plan.cc','run_migration_observe_guest.sh']
        test_identity=self.ssh('sha256sum '+' '.join(f'{DIRECTORY}/{n}' for n in testnames))
        (OUTPUT/'测试身份.sha256').write_text(test_identity);expected.update(hashes(test_identity))
        require((archive/'rocksdb_observe_test.cc').read_bytes()==(PROJECT/'build-femu/rrzc/migration-window-work/tests/rocksdb_observe_test.cc').read_bytes(),'已编译观察源与补丁副本不同')
        (OUTPUT/'本轮软件身份.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2))
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(expected)
        runtime_before=self.ssh(query);require(hashes(runtime_before)==expected,'运行业务前身份不符')
        (OUTPUT/'运行身份.before.sha256').write_text(runtime_before)
        collected=OUTPUT/'运行归档';collected.mkdir()
        try:
            self.record(阶段='构建及最小回归通过，单次180秒Greedy观察与全量最新值校验')
            self.root(f'set -e; test ! -e {RESULT}; nohup setsid bash {DIRECTORY}/run_migration_observe_guest.sh < /dev/null > {DIRECTORY}/launcher.log 2>&1 &')
            self.observe_until(f'{DIRECTORY}/finished',f'test ! -f {RESULT}/zones.csv || tail -1 {RESULT}/zones.csv; test ! -f {RESULT}/run.stdout.log || tail -1 {RESULT}/run.stdout.log',900)
        finally:
            remotes=[DIRECTORY]
            if self.ssh(f'if test -d {RESULT}; then printf yes; fi').strip()=='yes':remotes += [RESULT,RESULT+'-aux']
            errors=self.collect(collected,remotes)
            (OUTPUT/'运行身份.after.sha256').write_text(self.ssh(query))
        require(not errors and runtime_before==(OUTPUT/'运行身份.after.sha256').read_text(),'归档失败或运行中软件身份改变，保留客机')
        result=analyze(collected/Path(RESULT).name,expected,migration_observe=True)
        require(result['维护无错误'],'维护有错误，保留现场')
        (OUTPUT/'基础核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.record(阶段='完整业务、最新值及迁移账目通过，正常关闭本轮客机')
        self.shutdown();self.password=None
        self.record(阶段='单次Greedy迁移窗口观察完成并归档，等待独立信号分析',当前客机PID=None,已完成=['完整编译','内存与普通文件系统回归','真实业务与最新值校验','原始归档与基础核验'])

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    preflight()
    if args.check:print('新目标、停止父镜像、8092、KVM、空间及固定请求检查通过');return
    password=getpass.getpass('新隔离客机认证（仅驻留内存）：');OUTPUT.mkdir()
    run=MigrationObserve(SimpleNamespace(label=LABEL,seconds=180,space_weight=50),password,OUTPUT)
    try:run.run()
    except BaseException as error:
        run.record(阶段='停止，保留新客机与证据，不自动重跑',错误=str(error) or type(error).__name__);raise

if __name__=='__main__':main()
