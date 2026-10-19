#!/usr/bin/env python3
"""新子镜像构建独立采样，随后单次减少覆盖写的真实观察；失败不自动重跑。"""
import argparse,getpass,hashlib,json,os,socket
from pathlib import Path
from types import SimpleNamespace
from run_cost_rank_host import RankRun,running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_rocksdb_pressure_host import core_identity
from analyze_rocksdb_recent import LABEL,audit

OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'cost-rank-20261016-build.qcow2'
DIRECTORY=f'/home/femu/rrzc-{LABEL}-tests'
RESULT=f'/home/femu/rrzc-stream-results/{LABEL}'
TESTS=PROJECT/'research/rrzc/tests'
PORT='8091'
SOURCES=['rocksdb_observe_test.cc','rocksdb_observe_plan.h','test_rocksdb_observe_plan.cc',
         'run_rocksdb_recent_guest.sh','build_recent_sampler_guest.sh','test_recent_sampler.cc']

def preflight():
    require(not OUTPUT.exists() and PARENT.is_file() and not running_parent(PARENT) and Path('/dev/kvm').exists(),'输出、父镜像或KVM不符')
    with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,'8091已占用，保留旧客机')
    for p in (IMAGE_DIR/f'{LABEL}-pilot.qcow2',Path(f'/tmp/{LABEL}-pilot.pid'),
              Path(f'/tmp/{LABEL}-pilot.qmp'),Path(f'/tmp/{LABEL}-pilot-ssh.sock')):
        require(not p.exists(),'目标已存在：'+str(p))
    for p,minimum in ((IMAGE_DIR,8<<30),(PROJECT,2<<30)):
        s=os.statvfs(p);require(s.f_bavail*s.f_frsize>=minimum,'空间不足')

class RecentRun(RankRun):
    port=PORT
    label_prefix=LABEL
    parent_image=PARENT
    def scp(self):return ['scp','-q','-o',f'ControlPath={self.socket}','-o','BatchMode=yes','-P',self.port]
    def run(self):
        self.state.pop('实验空间权重百分比');self.state['模式顺序']=['构建','单次Greedy独立采样观察']
        plan=dict(初始键数=655360,值字节=4096,覆盖写秒数=90,读业务秒数=120,
            覆盖写键数=737280,读次数=48000,覆盖范围=262144,冷读键=524287,
            RR阈值=4096,GC启动比例=20,GC斜率=3,回收资格MiB=192,紧迫界限秒=10,额外预算MiB=16,
            独立采样='默认关闭，本轮显式开启；仅C2h查询与历史发布，不采集有效量或执行维护',
            原时间界限='最新同块前点，间隔0.5至2秒；不回找旧点，换块或Reset回退',
            实际选择='Greedy，独立约束仅影子，成本排序关闭',
            目标='RR期间继续采样，候选证据新鲜，真实业务及收尾压实／全量最新值通过；不保证出现收益',
            相对失败场景='明确减少25%覆盖写；不能与旧结果做性能因果比较',
            超时秒=900,自动重跑=False,旧客机处理='保留8080、8089、8090，独立使用8091')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        frozen=OUTPUT/'实验源';frozen.mkdir()
        sources=[TESTS/n for n in SOURCES]+[PROJECT/'research/rrzc/model/rrzc_recent_sampler.h',
            PROJECT/'research/rrzc/zenfs-recent-sampler.patch']
        self.command(['cp',*map(str,sources),str(Path(__file__).resolve()),str(frozen)])
        analysis=frozen/'分析';analysis.mkdir()
        self.command(['cp',str(PROJECT/'research/rrzc/analyze_rocksdb_recent.py'),
            str(PROJECT/'research/rrzc/analyze_rocksdb_observe.py'),str(analysis)])
        (OUTPUT/'宿主FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        self.record(阶段='固定独立采样观察计划已登记',固定计划=plan)
        self.start('pilot');base=core_identity()
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(base)
        identity=self.ssh(query)
        require({n:d for d,n in (s.split() for s in identity.splitlines())}==base,'父镜像生产身份不符')
        before=OUTPUT/'source.base.sha256';before.write_text(identity)
        self.ssh(f'test ! -e {DIRECTORY} && test ! -e {RESULT} && mkdir {DIRECTORY}')
        self.command(self.scp()+list(map(str,sources+[before]))+[f'femu@127.0.0.1:{DIRECTORY}/'])
        archive=OUTPUT/'运行归档';archive.mkdir()
        try:
            self.ssh(f'nohup setsid bash {DIRECTORY}/build_recent_sampler_guest.sh < /dev/null > {DIRECTORY}/build-launcher.log 2>&1 &')
            self.record(阶段='隔离新库编译／并发内存测试／普通文件系统真实数据库回归')
            self.observe_until(f'{DIRECTORY}/build.finished',f'tail -2 {DIRECTORY}/build-launcher.log; test ! -f {DIRECTORY}/library-build.log || tail -1 {DIRECTORY}/library-build.log',900)
            require(self.ssh(f'cat {DIRECTORY}/build.exit').strip()=='0','构建失败')
            after=self.ssh(f'cat {DIRECTORY}/source.after.sha256')
            actual={n:d for d,n in (s.split() for s in after.splitlines())}
            require(sorted(n for n,d in base.items() if actual.get(n)!=d)==['librocksdb_debug.a','plugin/zenfs/fs/fs_zenfs.cc'],'原核心修改范围超出接线源及库')
            require(actual['plugin/zenfs/fs/fs_zenfs.cc']==hashlib.sha256((PROJECT/'build-femu/rrzc/recent-sampler-work/fs/fs_zenfs.cc').read_bytes()).hexdigest(),'接线源不符')
            require(actual['plugin/zenfs/fs/rrzc_recent_sampler.h']==hashlib.sha256(sources[-2].read_bytes()).hexdigest(),'独立采样头不符')
            (OUTPUT/'生产身份.before.sha256').write_text(after)
            names=['rocksdb_observe_test','rocksdb_observe_test.cc','rocksdb_observe_plan.h','test_rocksdb_observe_plan.cc','run_rocksdb_recent_guest.sh']
            tests=self.ssh('sha256sum '+' '.join(f'{DIRECTORY}/{n}' for n in names))
            actual_tests={n:d for d,n in (s.split() for s in tests.splitlines())}
            require(all(actual_tests[f'{DIRECTORY}/{n}']==hashlib.sha256((TESTS/n).read_bytes()).hexdigest() for n in names[1:]),'测试源身份不符')
            (OUTPUT/'测试身份.sha256').write_text(tests)
            self.record(阶段='构建及最小回归通过，启动一次90秒写／120秒读真实观察')
            self.root(f'nohup setsid bash {DIRECTORY}/run_rocksdb_recent_guest.sh < /dev/null > {DIRECTORY}/launcher.log 2>&1 &')
            self.observe_until(f'{DIRECTORY}/finished',f'test ! -f {RESULT}/zones.csv || tail -1 {RESULT}/zones.csv; test ! -f {RESULT}/run.stdout.log || tail -1 {RESULT}/run.stdout.log',900)
        finally:
            remotes=[DIRECTORY]
            if self.ssh(f'if test -d {RESULT}; then printf yes; fi').strip()=='yes':remotes += [RESULT,RESULT+'-aux']
            errors=self.collect(archive,remotes)
            if (OUTPUT/'生产身份.before.sha256').exists():
                expected={n:d for d,n in (s.split() for s in (OUTPUT/'生产身份.before.sha256').read_text().splitlines())}
                (OUTPUT/'生产身份.after.sha256').write_text(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected)))
        require(not errors,'归档不完整，保留客机')
        result=audit(OUTPUT)
        (OUTPUT/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.record(阶段='真实数据与采样核验通过，正常关闭本轮客机')
        self.shutdown();self.password=None
        self.record(阶段='单次观察完成并归档，不是稳态或策略收益对照',当前客机PID=None,已完成=['编译','内存并发测试','真实数据库最小回归','单次ZNS观察及全量最新值校验'])

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    preflight()
    if args.check:print('独立8091端口、新子镜像及空间前置检查通过');return
    password=getpass.getpass('新隔离客机认证（只驻留内存）：');OUTPUT.mkdir()
    run=RecentRun(SimpleNamespace(label=LABEL,seconds=120,space_weight=50),password,OUTPUT)
    try:run.run()
    except Exception as error:run.record(阶段='停止，保留当前客机及原始证据，不自动重跑',错误=str(error));raise

if __name__=='__main__':main()
