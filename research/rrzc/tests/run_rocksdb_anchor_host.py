#!/usr/bin/env python3
"""同一生产库、固定条带锚点的一次机会验证；不自动重跑或调参数。"""
import argparse,getpass,hashlib,json,os,socket
from pathlib import Path
from types import SimpleNamespace
from run_cost_rank_host import RankRun,running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from analyze_rocksdb_anchor import LABEL,audit

OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'rocksdb-pair-rate-20261016-pilot.qcow2'
DIRECTORY=f'/home/femu/rrzc-{LABEL}-tests'
RESULT=f'/home/femu/rrzc-stream-results/{LABEL}'
TESTS=PROJECT/'research/rrzc/tests'
PORT='8091'
SOURCES=['rocksdb_observe_test.cc','rocksdb_observe_plan.h','test_rocksdb_observe_plan.cc',
         'test_rocksdb_anchor_plan.cc','run_rocksdb_anchor_guest.sh']

def core_identity():
    p=PROJECT/'research/rrzc/results/rocksdb-pair-rate-20261016/独立核验.json'
    return {n:d for n,d in json.loads(p.read_text())['软件身份'].items() if not n.startswith('/')}

def preflight():
    require(not OUTPUT.exists() and PARENT.is_file() and not running_parent(PARENT) and Path('/dev/kvm').exists(),'输出、父镜像或KVM不符')
    with socket.socket() as p:require(p.connect_ex(('127.0.0.1',int(PORT)))!=0,'8091占用，保留旧客机')
    for p in (IMAGE_DIR/f'{LABEL}-pilot.qcow2',Path(f'/tmp/{LABEL}-pilot.pid'),
              Path(f'/tmp/{LABEL}-pilot.qmp'),Path(f'/tmp/{LABEL}-pilot-ssh.sock')):
        require(not p.exists(),'目标已存在：'+str(p))
    for p,minimum in ((IMAGE_DIR,8<<30),(PROJECT,2<<30)):
        s=os.statvfs(p);require(s.f_bavail*s.f_frsize>=minimum,'空间不足')

class AnchorRun(RankRun):
    port=PORT
    label_prefix=LABEL
    parent_image=PARENT
    directory=DIRECTORY
    result=RESULT
    sources=SOURCES
    profile='anchors90'
    wrapper='run_rocksdb_anchor_guest.sh'
    plan_updates={}
    audit=staticmethod(audit)
    def scp(self):return ['scp','-q','-o',f'ControlPath={self.socket}','-o','BatchMode=yes','-P',self.port]
    def run(self):
        # 独立负载入口显式覆盖本轮路径，旧入口仍采用原固定计划。
        OUTPUT=self.output;DIRECTORY=self.directory;RESULT=self.result;SOURCES=self.sources
        self.state.pop('实验空间权重百分比');self.state['模式顺序']=['单次Greedy条带锚点机会验证']
        plan=dict(初始键数=655360,值字节=4096,覆盖写秒数=90,读业务秒数=120,覆盖写键数=737280,读次数=48000,
            固定条带数=4,条带键跨度=65536,各条带锚点区间='偏移32768至40959，保留不更新',
            总可写键数=229376,保留键数=32768,保留逻辑MiB=128,
            读请求='前45秒全部读取更新前缀16个热点；随后一半仍读前缀，一半轮流读四个条带锚点',
            四个锚点键=[36864,102400,167936,233472],锚点读次数=15000,
            RR阈值=4096,GC启动比例=20,GC斜率=3,回收资格MiB=192,额外预算MiB=16,紧迫界限秒=10,
            实际策略='Greedy，旧约束与双点估速均只做影子，不执行新建议',生产库='与上一轮完全同库，不重编、不修改',
            物理布局='固定逻辑条带不保证物理同区，必须用实际候选和SST片段日志验收',
            预登记验收='是否出现紧迫合格候选；是否出现预算内不同于Greedy的影子建议；负结果如实保留',
            对照边界='本轮只验证负载机会，不与旧请求不同的实验比较性能，不称为稳态或收益',
            自动重跑=False,固定观察超时秒=900,失败处理='归档原始数据并保留客机，不自动杀业务或重跑')
        plan.update(self.plan_updates)
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        frozen=OUTPUT/'实验源';frozen.mkdir()
        self.command(['cp',*[str(TESTS/n) for n in SOURCES],str(Path(__file__).resolve()),str(frozen)])
        analysis=frozen/'分析';analysis.mkdir()
        self.command(['cp',*[str(p) for p in (PROJECT/'research/rrzc').glob('*.py')],str(analysis)])
        self.record(阶段='固定条带锚点计划已登记，生产模型不变',固定计划=plan)
        self.start('pilot');core=core_identity()
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(core)
        before=self.ssh(query)
        require({n:d for d,n in (s.split() for s in before.splitlines())}==core,'生产库与上一轮不同')
        (OUTPUT/'生产身份.before.sha256').write_text(before)
        (OUTPUT/'宿主FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        self.ssh(f'test ! -e {DIRECTORY} && test ! -e {RESULT} && mkdir {DIRECTORY}')
        self.command(self.scp()+[str(TESTS/n) for n in SOURCES]+[f'femu@127.0.0.1:{DIRECTORY}/'])
        archive=OUTPUT/'运行归档';archive.mkdir()
        try:
            self.record(阶段='只编译新测试程序，不重编生产库')
            self.ssh(f'cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti '
                f'-DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude {DIRECTORY}/rocksdb_observe_test.cc librocksdb_debug.a '
                f'-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt -o {DIRECTORY}/rocksdb_observe_test '
                f'> {DIRECTORY}/build.log 2>&1 || status=$?; printf "%s\\n" "$status" > {DIRECTORY}/build.exit; exit "$status"',120)
            self.record(阶段='计划完整映射及普通文件系统跨锚点边界回归')
            self.ssh(f'set -e; for test in test_rocksdb_observe_plan test_rocksdb_anchor_plan; do '
                f'g++ -std=c++17 -Wall -Wextra -Werror {DIRECTORY}/$test.cc -o {DIRECTORY}/$test; '
                f'{DIRECTORY}/$test > {DIRECTORY}/$test.log; done; mkdir {DIRECTORY}/selftest; status=0; '
                f'RRZC_TEST_DB_PROFILE={self.profile} {DIRECTORY}/rocksdb_observe_test selftest {DIRECTORY}/selftest '
                f'> {DIRECTORY}/selftest.log 2>&1 || status=$?; printf "%s\\n" "$status" > {DIRECTORY}/selftest.exit; exit "$status"',120)
            names=['rocksdb_observe_test',*SOURCES]
            identity=self.ssh('sha256sum '+' '.join(f'{DIRECTORY}/{n}' for n in names))
            actual={n:d for d,n in (s.split() for s in identity.splitlines())}
            require(all(actual[f'{DIRECTORY}/{n}']==hashlib.sha256((TESTS/n).read_bytes()).hexdigest() for n in SOURCES),'测试源身份不符')
            (OUTPUT/'测试身份.sha256').write_text(identity)
            self.root(f'nohup setsid bash {DIRECTORY}/{self.wrapper} < /dev/null > {DIRECTORY}/launcher.log 2>&1 &')
            self.record(阶段='一次条带锚点真实业务／收尾压实／全量最新值校验')
            self.observe_until(f'{DIRECTORY}/finished',f'test ! -f {RESULT}/zones.csv || tail -1 {RESULT}/zones.csv; test ! -f {RESULT}/run.stdout.log || tail -1 {RESULT}/run.stdout.log',900)
        finally:
            remotes=[DIRECTORY]
            if self.ssh(f'if test -d {RESULT}; then printf yes; fi').strip()=='yes':remotes += [RESULT,RESULT+'-aux']
            errors=self.collect(archive,remotes)
            (OUTPUT/'生产身份.after.sha256').write_text(self.ssh(query))
        require(not errors,'归档不完整，保留客机')
        result=self.audit(OUTPUT);(OUTPUT/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.record(阶段='完整数据与机会核验完成，正常关闭本轮客机')
        self.shutdown();self.password=None
        self.record(阶段='单次固定机会验证完成，未执行新机制，未自动重跑',当前客机PID=None,已完成=['新负载编译','计划与普通文件系统回归','真实业务及独立最新值校验','机会覆盖核验'])

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    preflight()
    if args.check:print('停止的父镜像、新目标、8091及空间检查通过');return
    password=getpass.getpass('新隔离客机认证（只驻留内存）：');OUTPUT.mkdir()
    run=AnchorRun(SimpleNamespace(label=LABEL,seconds=120,space_weight=50),password,OUTPUT)
    try:run.run()
    except Exception as error:run.record(阶段='停止，保留客机与原始证据，不自动重跑',错误=str(error));raise

if __name__=='__main__':main()
