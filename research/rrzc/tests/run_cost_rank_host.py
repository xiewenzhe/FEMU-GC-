#!/usr/bin/env python3
"""新构建子镜像及三个新实验子镜像；同库同请求，只改变排序入口。"""
import argparse,getpass,hashlib,json,os,socket,time
from pathlib import Path
from types import SimpleNamespace
from run_tail_ablation_fixed_pair import FixedPair,PARENT,PORT,running_parent,expected_software
from run_stream_pair import PROJECT,IMAGE_DIR,require
from analyze_cost_rank import LABEL,DIRECTORY,hashes,analyze,compare

OUTPUT=PROJECT/'research/rrzc/results'/LABEL
BUILD_IMAGE=IMAGE_DIR/f'{LABEL}-build.qcow2'
TESTS=PROJECT/'research/rrzc/tests'

def preflight():
    require(PARENT.is_file() and not running_parent() and Path('/dev/kvm').exists(),'父镜像或KVM不符')
    with socket.socket() as p:require(p.connect_ex(('127.0.0.1',int(PORT)))!=0,'8090占用')
    require(not OUTPUT.exists(),'结果已存在，不覆盖')
    for mode in ('build','greedy','eta','cost'):
        for p in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
                  Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not p.exists(),'目标已存在：'+str(p))
    for p,minimum in ((IMAGE_DIR,16<<30),(PROJECT,3<<30)):
        s=os.statvfs(p);require(s.f_bavail*s.f_frsize>=minimum,'空间不足')
    previous=PROJECT/'research/rrzc/results/model-competition-20261015-greedy'
    for n in ('competition_workload_test.cc','competition_workload_plan.h'):
        require((TESTS/n).read_bytes()==(previous/n).read_bytes(),'原三候选负载改变')

class RankRun(FixedPair):
    label_prefix=LABEL
    def scp(self):return ['scp','-q','-o',f'ControlPath={self.socket}','-o','BatchMode=yes','-P',PORT]
    def collect(self,output,remotes):
        errors=[]
        for p in remotes:
            try:self.command(self.scp()+['-r',f'femu@127.0.0.1:{p}',str(output)],120)
            except Exception as error:errors.append(str(error))
        (output/'归档状态.json').write_text(json.dumps(dict(完整=not errors,错误=errors),ensure_ascii=False,indent=2))
        return errors
    def observe_until(self,marker,progress,limit):
        start=time.monotonic()
        while time.monotonic()-start<limit:
            message=self.ssh(f'if test -f {marker}; then printf "已结束\\n"; fi; {progress}; true')
            self.record(最近观测=message)
            if '已结束' in message:return
            time.sleep(15)
        raise RuntimeError('固定观察超时，保留客机和业务，不自动终止或重跑')
    def build(self):
        out=OUTPUT/'build';out.mkdir();self.start('build')
        expected=expected_software()
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(expected)
        identity=self.ssh(query)
        require({n:d for d,n in (s.split() for s in identity.splitlines())}==expected,'继承核心身份不符')
        base=out/'source.base.sha256';base.write_text(identity)
        self.ssh(f'test ! -e {DIRECTORY} && mkdir {DIRECTORY}')
        sources=[TESTS/n for n in ('build_cost_rank_guest.sh','run_cost_rank_guest.sh','test_rrzc_cost_rank.cc',
                    'competition_workload_test.cc','competition_workload_plan.h')]
        sources += [PROJECT/'research/rrzc/model/rrzc_cost_rank.h',PROJECT/'research/rrzc/zenfs-cost-rank.patch',base]
        self.command(self.scp()+list(map(str,sources))+[f'femu@127.0.0.1:{DIRECTORY}/'])
        try:
            self.ssh(f'nohup setsid bash {DIRECTORY}/build_cost_rank_guest.sh < /dev/null > {DIRECTORY}/launcher.log 2>&1 &')
            self.record(阶段='新客机库构建及内存测试中')
            self.observe_until(f'{DIRECTORY}/finished',f'tail -2 {DIRECTORY}/launcher.log',900)
        finally:errors=self.collect(out,[DIRECTORY])
        require(not errors,'构建归档失败')
        archive=out/Path(DIRECTORY).name
        require((archive/'build.exit').read_text().strip()=='0','构建失败，原始日志已归档')
        after=hashes(archive/'source.after.sha256')
        changed=[n for n in expected if expected[n]!=after.get(n)]
        require(sorted(changed)==['librocksdb_debug.a','plugin/zenfs/fs/fs_zenfs.cc'],'核心修改超出接线源及库')
        require(after['plugin/zenfs/fs/fs_zenfs.cc']==hashlib.sha256((PROJECT/'build-femu/rrzc/cost-rank-work/fs/fs_zenfs.cc').read_bytes()).hexdigest(),'接线源身份不同')
        require(after['plugin/zenfs/fs/rrzc_cost_rank.h']==hashlib.sha256(sources[-3].read_bytes()).hexdigest(),'排序头身份不同')
        self.record(阶段='新库构建及内存核验通过，正常关闭构建客机')
        self.shutdown();self.state['已完成'].append('build')
        # 源／负载／包装身份一同锁定；后续三组使用相同路径的同一二进制。
        for n in ('competition_workload_test.cc','competition_workload_plan.h','run_cost_rank_guest.sh'):
            after[f'{DIRECTORY}/{n}']=hashlib.sha256((archive/n).read_bytes()).hexdigest()
        return after
    def arm(self,mode,expected):
        out=OUTPUT/mode;out.mkdir();self.parent_image=BUILD_IMAGE;self.start(mode)
        result=f'/home/femu/rrzc-stream-results/{LABEL}-{mode}'
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(expected)
        identity=self.ssh(query);(out/'源码身份.before.sha256').write_text(identity)
        require({n:d for d,n in (s.split() for s in identity.splitlines())}==expected,'实验软件身份不符')
        try:
            self.root(f'set -e; test ! -e {result}; nohup setsid bash {DIRECTORY}/run_cost_rank_guest.sh {mode} '
                      f'< /dev/null > {DIRECTORY}/run-{mode}.log 2>&1 &')
            self.record(阶段=f'{mode}同库180秒业务及独立数据校验')
            self.observe_until(f'{DIRECTORY}/run-{mode}.finished',
                f'test ! -f {result}/states.csv || tail -1 {result}/states.csv',420)
        finally:
            errors=self.collect(out,[result,f'{DIRECTORY}/run-{mode}.log',f'{DIRECTORY}/run-{mode}.exit'])
            (out/'源码身份.after.sha256').write_text(self.ssh(query))
        require(not errors,'实验归档失败')
        require((out/f'run-{mode}.exit').read_text().strip()=='0','实验失败，证据已归档')
        require(identity==(out/'源码身份.after.sha256').read_text(),'实验期间核心改变')
        outcome=analyze(out/f'{LABEL}-{mode}',mode,expected)
        (out/'独立核验.json').write_text(json.dumps(outcome,ensure_ascii=False,indent=2))
        self.shutdown();self.state['已完成'].append(mode)
        self.record(阶段=f'{mode}完成核验并正常关机',当前客机PID=None)
        return outcome
    def run(self):
        self.state.pop('实验空间权重百分比');self.state['模式顺序']=['build','greedy','eta','cost']
        plan=dict(每组业务秒数=180,RR阈值=4096,GC启动比例=20,紧迫界限秒数=10,额外预算MiB=16,
            回收资格MiB=192,模式顺序=['greedy','eta','cost'],尾部约束='三组均保留，不启用消融',
            业务='原三候选负载，源码不变',唯一策略差异='Greedy不改选；ETA和成本组仅准入候选排序不同',
            评价='同请求同库、完整数据和逐轮安全核验；总与重复迁移、延迟、RR/ZC、终态空间，不要求正收益')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        self.record(阶段='同库三组固定计划登记',固定计划=plan)
        snapshot=OUTPUT/'分析源';snapshot.mkdir()
        self.command(['cp',*map(str,(PROJECT/'research/rrzc').glob('*.py')),str(snapshot)])
        self.command(['cp',str(Path(__file__).resolve()),str(snapshot)])
        (OUTPUT/'宿主FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        expected=self.build();(OUTPUT/'同库身份.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2))
        outcomes={mode:self.arm(mode,expected) for mode in ('greedy','eta','cost')}
        (OUTPUT/'三组对照.json').write_text(json.dumps(compare(outcomes),ensure_ascii=False,indent=2))
        self.password=None;self.record(阶段='三组短测及独立对照完成，全部本轮客机正常关闭',当前客机PID=None)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();preflight()
    if args.check:print('只读前置检查通过');return
    password=getpass.getpass('新实验客机认证（仅驻留内存）：');OUTPUT.mkdir()
    pair=RankRun(SimpleNamespace(label=LABEL,seconds=180,space_weight=50),password,OUTPUT)
    try:pair.run()
    except Exception as error:
        pair.record(阶段='停止，保留当前客机和已有证据，不自动重跑',错误=str(error));raise

if __name__=='__main__':main()
