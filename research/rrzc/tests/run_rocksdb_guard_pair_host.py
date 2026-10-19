#!/usr/bin/env python3
"""一次构建测试二进制、两个全新子镜像的固定短配对；不改生产库，不自动重跑。"""
import argparse,getpass,hashlib,json,os,socket
from pathlib import Path
from types import SimpleNamespace
from run_rocksdb_anchor_slow_host import SlowRun
from run_rocksdb_anchor_host import PARENT,TESTS,core_identity
from run_cost_rank_host import running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from analyze_rocksdb_guard_pair import LABEL,DIRECTORY,RESULT,analyze_arm,compare

OUTPUT=PROJECT/'research/rrzc/results'/LABEL
BUILD_IMAGE=IMAGE_DIR/f'{LABEL}-build.qcow2'
SOURCES=['rocksdb_observe_test.cc','rocksdb_observe_plan.h','test_rocksdb_observe_plan.cc',
         'test_rocksdb_anchor_plan.cc','run_rocksdb_guard_pair_guest.sh']
PORT='8092'

def preflight():
    require(not OUTPUT.exists() and PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR and not running_parent(PARENT) and Path('/dev/kvm').exists(),'父镜像、输出或KVM状态不符')
    with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,'8092占用，保留已有客机')
    for mode in ('build','greedy','guarded'):
        for p in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
                  Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not p.exists(),'目标已存在：'+str(p))
    for p,minimum in ((IMAGE_DIR,12<<30),(PROJECT,3<<30)):
        s=os.statvfs(p);require(s.f_bavail*s.f_frsize>=minimum,'空间不足')
    previous=PROJECT/'research/rrzc/results/rocksdb-anchor-slow-20261016/实验源'
    for name in SOURCES[:-1]:require((TESTS/name).read_bytes()==(previous/name).read_bytes(),'已完整跑通的负载或计划已变')

def hashes(text):return {n:d for d,n in (s.split() for s in text.splitlines())}

class GuardPairRun(SlowRun):
    label_prefix=LABEL
    parent_image=PARENT
    directory=DIRECTORY
    result=RESULT
    port=PORT
    def build(self):
        output=OUTPUT/'build';output.mkdir();self.start('build');core=core_identity()
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(core)
        before=self.ssh(query);require(hashes(before)==core,'生产源或库身份不同')
        (output/'生产身份.before.sha256').write_text(before)
        self.ssh(f'test ! -e {DIRECTORY} && test ! -e {RESULT} && test ! -e {RESULT}-aux && mkdir {DIRECTORY}')
        self.command(self.scp()+[str(TESTS/n) for n in SOURCES]+[f'femu@127.0.0.1:{DIRECTORY}/'])
        try:
            self.record(阶段='仅编译一次固定负载测试程序，生产库不重编')
            self.ssh(f'cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti '
                f'-DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude {DIRECTORY}/rocksdb_observe_test.cc librocksdb_debug.a '
                f'-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt -o {DIRECTORY}/rocksdb_observe_test '
                f'> {DIRECTORY}/build.log 2>&1 || status=$?; printf "%s\\n" "$status" > {DIRECTORY}/build.exit; exit "$status"',120)
            self.ssh(f'set -e; for test in test_rocksdb_observe_plan test_rocksdb_anchor_plan; do '
                f'g++ -std=c++17 -Wall -Wextra -Werror {DIRECTORY}/$test.cc -o {DIRECTORY}/$test; '
                f'{DIRECTORY}/$test > {DIRECTORY}/$test.log; done; mkdir {DIRECTORY}/selftest; status=0; '
                f'RRZC_TEST_DB_PROFILE=anchors180slow {DIRECTORY}/rocksdb_observe_test selftest {DIRECTORY}/selftest '
                f'> {DIRECTORY}/selftest.log 2>&1 || status=$?; printf "%s\\n" "$status" > {DIRECTORY}/selftest.exit; exit "$status"',120)
            test_identity=self.ssh('sha256sum '+' '.join(f'{DIRECTORY}/{n}' for n in ['rocksdb_observe_test',*SOURCES]))
            expected=hashes(test_identity)
            require(all(expected[f'{DIRECTORY}/{n}']==hashlib.sha256((TESTS/n).read_bytes()).hexdigest() for n in SOURCES),'固定测试源身份不符')
            (output/'测试身份.sha256').write_text(test_identity)
        finally:
            errors=self.collect(output,[DIRECTORY])
            (output/'生产身份.after.sha256').write_text(self.ssh(query))
        require(not errors and before==(output/'生产身份.after.sha256').read_text(),'构建归档或生产身份核验失败')
        expected.update(core)
        self.record(阶段='固定二进制编译及普通文件系统回归通过，关闭构建客机')
        self.shutdown();self.state['已完成'].append('build');self.record(当前客机PID=None)
        return expected
    def arm(self,mode,expected):
        output=OUTPUT/mode;output.mkdir();self.parent_image=BUILD_IMAGE;self.start(mode)
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(expected)
        before=self.ssh(query);require(hashes(before)==expected,'配对软件／二进制身份不同')
        (output/'生产身份.before.sha256').write_text(before)
        archive=output/'运行归档';archive.mkdir()
        try:
            self.root(f'set -e; test ! -e {RESULT}; test ! -e {RESULT}-aux; '
                f'nohup setsid bash {DIRECTORY}/run_rocksdb_guard_pair_guest.sh {mode} < /dev/null > {DIRECTORY}/launcher.log 2>&1 &')
            self.record(阶段=f'{mode}固定180秒业务、收尾及独立校验')
            self.observe_until(f'{DIRECTORY}/finished',
                f'test ! -f {RESULT}/zones.csv || tail -1 {RESULT}/zones.csv; test ! -f {RESULT}/run.stdout.log || tail -1 {RESULT}/run.stdout.log',900)
        finally:
            remotes=[DIRECTORY]
            if self.ssh(f'if test -d {RESULT}; then printf yes; fi').strip()=='yes':remotes += [RESULT,RESULT+'-aux']
            errors=self.collect(archive,remotes)
            (output/'生产身份.after.sha256').write_text(self.ssh(query))
        require(not errors,'本组归档不完整，保留客机')
        result=analyze_arm(output,mode,expected)
        (output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.record(阶段=f'{mode}数据、选择与维护账目核验通过，正常关机')
        self.shutdown();self.state['已完成'].append(mode);self.record(当前客机PID=None)
        return result
    def run(self):
        self.state.pop('实验空间权重百分比');self.state['模式顺序']=['build','greedy','guarded']
        plan=dict(每组写阶段秒数=180,写速率键每秒=4096,写键数=737280,读阶段秒数=120,读次数=48000,
            初始键数=655360,值字节=4096,请求计划='原anchors180slow源码逐字节不变',
            策略差异='仅ZENFS_RRZC_GUARDED_MODE=shadow／execute',
            机制='现有短期紧迫度＋额外搬运预算＋尾部和源复核；不是双点估速真实执行或新成本预测模型',
            RR阈值=4096,GC启动比例=20,GC斜率=3,回收资格MiB=192,紧迫界限秒=10,额外预算MiB=16,
            双点影子='两组均关闭，仅供影子的模块不接入执行',成本排序='两组均关闭，现有ETA优先规则不变',
            身份控制='生产库不重编；只编译一次测试程序，两个子镜像继承同一停止构建父镜像的同一二进制',
            验收='全量最新值、同请求同软件、逐轮复核、实际改选、RR/ZC、总迁移、请求封装均值与分位、末次IO空间',
            重复迁移='当前无精确逻辑片段重复账目，保持未知，不用同名SST次数替代',
            结论边界='单次短配对，未改选则不能检验收益；负结果保留，不称稳态、普遍收益或统计显著',
            自动重跑=False,每组固定观察超时秒=900,失败处理='明确错误提前归档，保留客机和业务；不杀进程或自动进入下一组')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        frozen=OUTPUT/'实验源';frozen.mkdir();(frozen/'分析').mkdir()
        self.command(['cp',*[str(TESTS/n) for n in SOURCES],str(Path(__file__).resolve()),str(frozen)])
        self.command(['cp',*[str(p) for p in (PROJECT/'research/rrzc').glob('*.py')],str(frozen/'分析')])
        (OUTPUT/'宿主FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        self.record(阶段='固定短配对计划登记，不改生产机制或负载',固定计划=plan)
        expected=self.build();(OUTPUT/'同库身份.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2))
        arms={mode:self.arm(mode,expected) for mode in ('greedy','guarded')}
        result=dict(汇总=compare(arms),各组=arms)
        (OUTPUT/'配对核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.password=None;self.record(阶段='固定短配对完成并归档，本轮客机均正常关闭',当前客机PID=None)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    preflight()
    if args.check:print('只读前置检查通过，停止父镜像、新目标、8092和原固定负载均符合要求');return
    password=getpass.getpass('新隔离客机认证（只驻留内存）：');OUTPUT.mkdir()
    run=GuardPairRun(SimpleNamespace(label=LABEL,seconds=180,space_weight=50),password,OUTPUT)
    try:run.run()
    except BaseException as error:
        run.record(阶段='停止，保留客机与证据，不自动重跑或进入下一组',错误=str(error) or type(error).__name__);raise

if __name__=='__main__':main()
