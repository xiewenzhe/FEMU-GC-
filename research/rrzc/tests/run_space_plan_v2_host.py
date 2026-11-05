#!/usr/bin/env python3
"""修正同成本首区顺序；保留第一版全部结果，用相同YCSB重新串行验收。"""
import argparse
import getpass
import json
from pathlib import Path
import signal
import run_joint_host as base
from run_joint_host import require,PROJECT,IMAGE_DIR
from run_window_credit_host import resources,digest
from run_window_ycsb_host import WindowYcsbRun,STAGES
from run_strict_ycsb_host import FILES as OLD_FILES
from run_strict_rr_build_host import StrictBuild
from analyze_space_ycsb import MODES,analyze_arm,compare_three

LABEL='joint-space-plan-v2-20261022'
SOURCE=IMAGE_DIR/'joint-space-plan-v1-20261022-results'
PARENT=IMAGE_DIR/'joint-space-plan-v1-20261022-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
ROOT=PROJECT/'research/rrzc';TESTS=ROOT/'tests'
FILES=[p for p in OLD_FILES if p.name not in ('build_strict_ycsb_guest.sh','run_strict_ycsb_guest.sh',
    'capture_expected_ycsb_guest.py')]
FILES += [TESTS/n for n in ('build_space_plan_v2_guest.sh','run_space_plan_guest.sh',
    'capture_space_plan_guest.py','test_space_plan.cc')]
FILES += [ROOT/'model/rrzc_space_plan.h',ROOT/'zenfs-space-plan-v2.patch']

def versioned_arm(root,stage,mode):
    return analyze_arm(root,stage,mode,version=2)

class SpaceRun(WindowYcsbRun,StrictBuild):
    files=FILES
    manifest=MANIFEST
    modes=MODES
    capture_adapter='capture_space_plan_guest.py'
    build_script='build_space_plan_v2_guest.sh'
    run_script='run_space_plan_guest.sh'
    title='空间方案第二版'
    analyze=staticmethod(versioned_arm)
    allowed_build_changes=('plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','plugin/zenfs/fs/rrzc_space_plan.h','librocksdb_debug.a')
    extra_dependencies=(Path(__file__).resolve(),TESTS/'run_strict_rr_build_host.py',TESTS/'run_strict_ycsb_host.py')

    def freeze_extra(self,frozen):
        target=frozen/'模型';target.mkdir()
        for p in (ROOT/'model').glob('*.h'):(target/p.name).write_bytes(p.read_bytes())
        # raw预测核重算器仍从相同冻结版本重建，不能只校验新方案自己的输出。
        p=TESTS/'test_expected_rr_selector.cc';(frozen/p.name).write_bytes(p.read_bytes())

    def build(self):
        super().build()
        manifest=json.loads((self.output/'同库身份.json').read_text())
        require(manifest['plugin/zenfs/fs/rrzc_space_plan.h']==digest(self.output/'源码/rrzc_space_plan.h'),
            '实际空间方案核与冻结源码不同')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true')
    p.add_argument('--label',default=LABEL);a=p.parse_args();a.phase='build';a.scenarios=[]
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(a);before=resources(profile)
    state=json.loads((SOURCE/'状态.json').read_text())
    require(state['当前客机PID'] is None and '全部客机关闭' in state['阶段'],'源实验未收尾')
    binary=digest(PROJECT/'build-femu/qemu-system-x86_64')
    require(binary==json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'],'FEMU版本漂移')
    for s in STAGES:
        for m in MODES:
            tag=a.label+'-'+s+'-'+m
            for path in (IMAGE_DIR/(tag+'.qcow2'),Path('/tmp')/(tag+'.pid'),
                         Path('/tmp')/(tag+'.qmp'),Path('/tmp')/(tag+'-ssh.sock')):
                require(not path.exists(),'拒绝覆盖：'+str(path))
    if a.check:print(json.dumps(dict(预检通过=True,资源=before),ensure_ascii=False,indent=2));return
    password=getpass.getpass('空间方案YCSB隔离实验认证（仅内存）：');output.mkdir()
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    plan=dict(父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=binary,初始资源=before,
        阶段=['build',*[s+'-'+m for s in STAGES for m in MODES]],
        同分修正='预计总复制、计划当前复制和计划区数相同时，优先当前首区复制更小者，再比较首区回收量；不先固定Greedy；旧版本默认行为保留',
        改动='仅显式开启空间方案时：真实字节缺口为零且无分配等待可不选区；每候选作首区计算补足当前空间所需总复制及未处理候选预计RR复制',
        近似='补充区按当前复制/回收比排列，非组合全局最优；只执行首区；未来RR预计回收另列，不抵扣当前缺口；原RR预测假设不变',
        三组={'greedy':'原动态候选+原Greedy', 'space':'扩展满区正回收候选+空间方案，无RR抵扣',
            'model':'同space候选与需求+原期望RR预测进入方案总成本'},
        保持='4GiB功能盘/32区/128MiB区/128块每区、GC20%外部检查、严格RR1024、迁移安全规则及旧入口默认行为',
        负载='YCSB-C++公开派生，同种子20261017单线程50/50读更新，不限速、无WAL、无块缓存；8192请求小测后每组400000请求',
        观测='真实迁移、RR/ZC、Reset与推导块擦除、平均及P99/P99.9/P99.99API延迟、Run及前台吞吐、业务末态硬件空间',
        验收='编译及UBSan纯核、四次POSIX自测、小测三组后才短测；独立整数方案与原概率评分重算、完整全键数据校验',
        收尾='默认单台串行，约8GiB加16GiB余量；成功失败均采证、哈希核验、正常关机及PID/SSH退出，不自动重跑',
        边界='非32GiB正式规模、非稳态；同qcow2不代表同NAND内存；RR减少不是唯一收益判据，终态空间必须一起看')
    (output/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    r=SpaceRun(a,password,output,profile);r.qemu_digest=binary
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        r.build()
        for s in STAGES:
            for m in MODES:r.arm(s,m)
            result=compare_three(output,s,version=2)
            (output/(s+'三组核验.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
            r.record(阶段=s+'三组完成，已分析且全部客机关闭',最近差值=result['相对原Greedy差值'])
        r.record(阶段='空间方案YCSB小测及40万三组完成，全部客机关闭')
    except BaseException as e:r.record(阶段='空间方案实验停止，保留原始错误且不自动重跑',错误=str(e));raise
    finally:r.password=None

if __name__=='__main__':main()
