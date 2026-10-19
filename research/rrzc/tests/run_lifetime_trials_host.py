#!/usr/bin/env python3
"""固定六组串行复验，保留全部结果；失败停止，不自动重跑或清理现场。"""
import argparse,getpass,json,os,socket
from pathlib import Path
from types import SimpleNamespace
from run_lifetime_host import LifetimeRun,PARENT,OLD,TESTS
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_cost_rank_host import PORT,running_parent
from analyze_lifetime import compare
from analyze_lifetime_trials import summarize

LABEL='lifetime-validation-20261016'
OUTPUT=PROJECT/'research/rrzc/results'/LABEL
ORIGINAL=PROJECT/'research/rrzc/results/lifetime-20261016'
TRIALS=('repeat','delay20')
SOURCES=['lifetime_workload_test.cc','lifetime_workload_plan.h','competition_workload_plan.h','run_lifetime_trials_guest.sh']

def preflight():
    require(PARENT.is_file() and not running_parent(PARENT) and Path('/dev/kvm').exists(),'父镜像或KVM不符')
    require(json.loads((ORIGINAL/'状态.json').read_text())['已完成']==['greedy','eta','cost'],'原配对未完成')
    require(not OUTPUT.exists(),'本轮结果已存在')
    with socket.socket() as p:require(p.connect_ex(('127.0.0.1',int(PORT)))!=0,'8090占用')
    for trial in TRIALS:
        for mode in ('greedy','eta','cost'):
            label=f'lifetime-{trial}-20261016-{mode}'
            for p in (IMAGE_DIR/f'{label}.qcow2',Path(f'/tmp/{label}.pid'),Path(f'/tmp/{label}.qmp'),Path(f'/tmp/{label}-ssh.sock')):
                require(not p.exists(),'目标已存在：'+str(p))
    for p,minimum in ((IMAGE_DIR,16<<30),(PROJECT,4<<30)):
        s=os.statvfs(p);require(s.f_bavail*s.f_frsize>=minimum,'空间不足')

class TrialsRun(LifetimeRun):
    guest_directory=f'/home/femu/rrzc-{LABEL}-tests'
    sourcefiles=SOURCES
    guest_wrapper='run_lifetime_trials_guest.sh'
    def run(self):
        self.state.pop('实验空间权重百分比')
        order=[f'{t}/{m}' for t in TRIALS for m in ('greedy','eta','cost')]
        plan=dict(固定执行顺序=order,每组秒数=180,停止读取相对代际秒=67,
            原时序删除相对代际秒=68,推迟删除相对代际秒=88,RR阈值=4096,GC启动比例=20,
            生产模型修改=False,各组同一负载二进制=True,只改变删除时刻=True,
            不根据结果调参=True,失败自动重跑=False,目的='原8MiB净节省能否重复，以及对删除时序是否敏感')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        snapshot=OUTPUT/'实验源';snapshot.mkdir()
        self.command(['cp',*[str(TESTS/n) for n in SOURCES],str(Path(__file__).resolve()),
                      str(TESTS/'run_lifetime_host.py'),str(snapshot)])
        (snapshot/'analysis').mkdir()
        self.command(['cp',*map(str,(PROJECT/'research/rrzc').glob('*.py')),str(snapshot/'analysis')])
        (OUTPUT/'宿主FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        self.record(阶段='六组固定计划已登记',模式顺序=order,固定计划=plan)
        core={n:d for n,d in json.loads((OLD/'同库身份.json').read_text()).items() if not n.startswith('/')}
        shared=None;outcomes={}
        for trial in TRIALS:
            self.trial=trial;self.label_prefix=f'lifetime-{trial}-20261016'
            self.result_root=OUTPUT/trial;self.result_root.mkdir();arms={}
            for mode in ('greedy','eta','cost'):
                arms[mode],archive=self.arm(mode,core,shared)
                if shared is None:shared=archive
                self.state['已完成'][-1]=f'{trial}/{mode}'
                self.record(阶段=f'{trial}/{mode}已核验并关闭客机')
            outcomes[trial]=compare(arms)
            (self.result_root/'三组对照.json').write_text(json.dumps(outcomes[trial],ensure_ascii=False,indent=2))
        original=json.loads((ORIGINAL/'三组对照.json').read_text())
        result=summarize(original,outcomes)
        (OUTPUT/'重复性与时序对照.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.password=None;self.record(阶段='六组及重复性／时序对照完成，本轮客机全部正常关闭',当前客机PID=None)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();preflight()
    if args.check:print('六组只读前置检查通过');return
    password=getpass.getpass('本轮新客机认证（仅驻留内存）：');OUTPUT.mkdir()
    pair=TrialsRun(SimpleNamespace(label=LABEL,seconds=180,space_weight=50),password,OUTPUT)
    try:pair.run()
    except Exception as error:
        pair.record(阶段='停止，保留当前客机和证据，没有自动重跑',错误=str(error));raise

if __name__=='__main__':main()
