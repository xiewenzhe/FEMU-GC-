#!/usr/bin/env python3
"""固定半速写入的一次Greedy条带观察，明确失败后归档但不杀客机业务。"""
import argparse,getpass,json,socket,time
from pathlib import Path
from types import SimpleNamespace
from run_rocksdb_anchor_host import AnchorRun,PARENT,TESTS,core_identity
from run_cost_rank_host import running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from analyze_rocksdb_anchor import audit

LABEL='rocksdb-anchor-slow-20261016'
OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PORT='8092'

def preflight():
    require(not OUTPUT.exists() and PARENT.is_file() and not running_parent(PARENT) and Path('/dev/kvm').exists(),'输出、父镜像或KVM不符')
    with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,'8092占用，不操作已有客机')
    for p in (IMAGE_DIR/f'{LABEL}-pilot.qcow2',Path(f'/tmp/{LABEL}-pilot.pid'),
              Path(f'/tmp/{LABEL}-pilot.qmp'),Path(f'/tmp/{LABEL}-pilot-ssh.sock')):
        require(not p.exists(),'目标已存在：'+str(p))
    import os
    for p,minimum in ((IMAGE_DIR,8<<30),(PROJECT,2<<30)):
        s=os.statvfs(p);require(s.f_bavail*s.f_frsize>=minimum,'空间不足')
    require(bool(core_identity()),'生产身份为空')

def fatal_evidence(message):
    return any(s in message for s in ('Zone allocation failure','业务后台错误'))

class SlowRun(AnchorRun):
    port=PORT
    label_prefix=LABEL
    directory=f'/home/femu/rrzc-{LABEL}-tests'
    result=f'/home/femu/rrzc-stream-results/{LABEL}'
    wrapper='run_rocksdb_anchor_slow_guest.sh'
    sources=['rocksdb_observe_test.cc','rocksdb_observe_plan.h','test_rocksdb_observe_plan.cc',
             'test_rocksdb_anchor_plan.cc',wrapper]
    profile='anchors180slow'
    plan_updates=dict(覆盖写秒数=180,覆盖写速率键每秒=4096,混合阶段计划秒数=180,
        唯一负载变化='写速率8192减为4096键每秒，总写量737280不变，写阶段90延长至180秒',
        时序边界='读计划仍120秒48000次，最后60秒只写；因此不与旧负载比较策略性能',
        实验目的='验证固定降峰后能否完整结束，再观察真实候选；不提前调整模型预算',
        失败处理='发现明确分配失败或后台错误即结束宿主等待并归档，不杀业务、不关闭客机、不重跑')
    audit=staticmethod(lambda root:audit(root,slow=True))
    def run(self):
        # 保留独立入口源，父类再冻结公用包装器和分析源。
        (self.output/'限速入口.py').write_bytes(Path(__file__).read_bytes())
        super().run()
    def observe_until(self,marker,progress,limit):
        start=time.monotonic()
        while time.monotonic()-start<limit:
            message=self.ssh(f'if test -f {marker}; then printf "已结束\\n"; fi; '
                f'test ! -f {self.result}-aux/rrzc-real/LOG || grep -F -m 1 "Zone allocation failure" {self.result}-aux/rrzc-real/LOG; '
                f'test ! -f {self.result}/events.csv || awk -F, \'$4=="后台错误" {{print "业务后台错误"; exit}}\' {self.result}/events.csv; '
                f'{progress}; true')
            self.record(最近观测=message)
            require(not fatal_evidence(message),'发现明确业务错误，提前归档但保留现场，不自动终止或重跑')
            if '已结束' in message:return
            time.sleep(15)
        raise RuntimeError('固定观察超时，保留客机和业务，不自动终止或重跑')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    preflight()
    if args.check:print('只读前置检查通过，8092空闲；未启动客机或业务');return
    password=getpass.getpass('新隔离客机认证（只驻留内存）：');OUTPUT.mkdir()
    run=SlowRun(SimpleNamespace(label=LABEL,seconds=180,space_weight=50),password,OUTPUT)
    try:run.run()
    except BaseException as error:
        run.record(阶段='停止，保留客机与原始证据，不自动重跑',错误=str(error) or type(error).__name__);raise

if __name__=='__main__':main()
