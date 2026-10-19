#!/usr/bin/env python3
"""复用已验证库，新隔离三组失效时序实验；只编译一次相同负载程序。"""
import argparse,getpass,hashlib,json,os,socket
from pathlib import Path
from types import SimpleNamespace
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_cost_rank_host import RankRun,PORT,running_parent
from lifetime_plan import LABEL,DIRECTORY
from analyze_lifetime import analyze,compare

OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'cost-rank-20261016-build.qcow2'
OLD=PROJECT/'research/rrzc/results/cost-rank-20261016'
TESTS=PROJECT/'research/rrzc/tests'
SOURCES=['lifetime_workload_test.cc','lifetime_workload_plan.h','competition_workload_plan.h','run_lifetime_guest.sh']

def preflight():
    require(PARENT.is_file() and not running_parent(PARENT) and Path('/dev/kvm').exists(),'父镜像或KVM不符')
    require(json.loads((OLD/'状态.json').read_text())['已完成']==['build','greedy','eta','cost'],'上轮没有完整结束')
    with socket.socket() as p:require(p.connect_ex(('127.0.0.1',int(PORT)))!=0,'8090占用')
    require(not OUTPUT.exists(),'结果目录已存在')
    for mode in ('greedy','eta','cost'):
        for p in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
                  Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not p.exists(),'目标已存在：'+str(p))
    for p,minimum in ((IMAGE_DIR,8<<30),(PROJECT,2<<30)):
        s=os.statvfs(p);require(s.f_bavail*s.f_frsize>=minimum,'空间不足')

class LifetimeRun(RankRun):
    label_prefix=LABEL
    parent_image=PARENT
    guest_directory=DIRECTORY
    result_root=OUTPUT
    sourcefiles=SOURCES
    guest_wrapper='run_lifetime_guest.sh'
    trial=None
    def arm(self,mode,core,shared):
        directory=self.guest_directory
        out=self.result_root/mode;out.mkdir();self.start(mode)
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(core)
        require({n:d for d,n in (s.split() for s in self.ssh(query).splitlines())}==core,'生产库或源码改变')
        self.ssh(f'test ! -e {directory} && mkdir {directory}')
        self.command(self.scp()+[str(TESTS/n) for n in self.sourcefiles]+[f'femu@127.0.0.1:{directory}/'])
        if shared is None:
            self.record(阶段='Greedy客机编译新负载，生产库不重编')
            self.ssh(f'cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti '
                f'-DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude {directory}/lifetime_workload_test.cc librocksdb_debug.a '
                f'-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt -o {directory}/lifetime_workload_test '
                f'> {directory}/build.log 2>&1 || status=$?; printf "%s\\n" "$status" > {directory}/build.exit; exit "$status"',120)
        else:
            self.command(self.scp()+[str(shared/'lifetime_workload_test'),str(shared/'build.exit'),f'femu@127.0.0.1:{directory}/'])
        expected=dict(core)
        for n in self.sourcefiles:expected[f'{directory}/{n}']=hashlib.sha256((TESTS/n).read_bytes()).hexdigest()
        binary=self.ssh(f'sha256sum {directory}/lifetime_workload_test').split()[0]
        if shared is not None:require(binary==hashlib.sha256((shared/'lifetime_workload_test').read_bytes()).hexdigest(),'负载二进制不同')
        expected[f'{directory}/lifetime_workload_test']=binary
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(expected)
        identity=self.ssh(query);(out/'源码身份.before.sha256').write_text(identity)
        require({n:d for d,n in (s.split() for s in identity.splitlines())}==expected,'测试源或库身份不符')
        result=f'/home/femu/rrzc-stream-results/{self.label_prefix}-{mode}'
        argument=f' {self.trial}' if self.trial else ''
        marker=f'run-{self.trial}-{mode}' if self.trial else f'run-{mode}'
        try:
            self.root(f'set -e; test ! -e {result}; nohup setsid bash {directory}/{self.guest_wrapper} {mode}{argument} '
                      f'< /dev/null > {directory}/{marker}.log 2>&1 &')
            self.record(阶段=f'{mode}固定失效时序业务和数据校验')
            self.observe_until(f'{directory}/{marker}.finished',f'test ! -f {result}/states.csv || tail -1 {result}/states.csv',420)
        finally:
            errors=self.collect(out,[result,directory])
            (out/'源码身份.after.sha256').write_text(self.ssh(query))
        require(not errors,'归档不完整')
        archive=out/Path(directory).name
        require((archive/f'{marker}.exit').read_text().strip()=='0','业务失败，保留现场')
        require(identity==(out/'源码身份.after.sha256').read_text(),'业务期间软件改变')
        outcome=analyze(out/f'{self.label_prefix}-{mode}',mode,expected,trial=self.trial)
        (out/'独立核验.json').write_text(json.dumps(outcome,ensure_ascii=False,indent=2))
        self.shutdown();self.state['已完成'].append(mode)
        self.record(阶段=f'{mode}核验完成，新客机正常关机',当前客机PID=None)
        return outcome,archive
    def run(self):
        self.state.pop('实验空间权重百分比');self.state['模式顺序']=['greedy','eta','cost']
        plan=dict(每组秒数=180,负载='每三代冷区早退役／正常寿命／热区早退役',早退役停止读取年龄秒=67,
                  早退役删除年龄秒=68,GC启动比例=20,RR阈值=4096,额外预算MiB=16,紧迫界限秒数=10,
                  生产模型修改=False,选择器知道未来寿命=False,无自动重跑=True,
                  目标='逐文件识别真正少搬与过早搬运；不要求正收益，不作为稳态实验')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        snapshot=OUTPUT/'实验源';snapshot.mkdir()
        self.command(['cp',*[str(TESTS/n) for n in SOURCES],str(Path(__file__).resolve()),
                      str(PROJECT/'research/rrzc/lifetime_plan.py'),str(PROJECT/'research/rrzc/analyze_lifetime.py'),str(snapshot)])
        (snapshot/'analysis').mkdir()
        self.command(['cp',*map(str,(PROJECT/'research/rrzc').glob('*.py')),str(snapshot/'analysis')])
        self.record(阶段='失效时序计划已固定',固定计划=plan)
        core={n:d for n,d in json.loads((OLD/'同库身份.json').read_text()).items() if not n.startswith('/')}
        shared=None;arms={}
        for mode in ('greedy','eta','cost'):
            arms[mode],archive=self.arm(mode,core,shared)
            if shared is None:shared=archive
        (OUTPUT/'三组对照.json').write_text(json.dumps(compare(arms),ensure_ascii=False,indent=2))
        self.password=None;self.record(阶段='失效时序三组核验及逐文件对照完成，本轮客机全部关闭',当前客机PID=None)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();preflight()
    if args.check:print('只读前置检查通过');return
    password=getpass.getpass('本轮新客机认证（仅驻留内存）：');OUTPUT.mkdir()
    pair=LifetimeRun(SimpleNamespace(label=LABEL,seconds=180,space_weight=50),password,OUTPUT)
    try:pair.run()
    except Exception as error:
        pair.record(阶段='停止，保留当前客机和证据，不自动重跑',错误=str(error));raise

if __name__=='__main__':main()
