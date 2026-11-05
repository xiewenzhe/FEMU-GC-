#!/usr/bin/env python3
"""严格RR双方同规则：先小测，再运行原40万不限速YCSB，逐阶段归档关机。"""
import argparse
import getpass
import json
from pathlib import Path
import signal
import run_joint_host as base
from run_joint_host import require,PROJECT,IMAGE_DIR
from run_window_credit_host import resources,digest
from run_expected_ycsb_host import ExpectedYcsbRun,FILES as OLD_FILES,STAGES,MODES
from run_strict_rr_build_host import StrictBuild
from analyze_expected_ycsb import analyze_arm,compare_pair

LABEL='joint-strict-ycsb-v3-20261022'
SOURCE=IMAGE_DIR/'joint-strict-rr-build-v1-20261022-results'
PARENT=IMAGE_DIR/'joint-strict-rr-build-v1-20261022-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
ROOT=PROJECT/'research/rrzc';TESTS=ROOT/'tests'
FILES=[p for p in OLD_FILES if p.name not in ('build_expected_ycsb_guest.sh','run_expected_ycsb_guest.sh','verify_ycsb.cc')]
FILES += [TESTS/n for n in ('build_strict_ycsb_guest.sh','run_strict_ycsb_guest.sh','strict_stats_guest.py')]
FILES += [TESTS/'strict/verify_ycsb.cc']

def strict_arm(root,stage,mode):return analyze_arm(root,stage,mode,strict_rr=True)

class StrictYcsbRun(ExpectedYcsbRun,StrictBuild):
    files=FILES
    manifest=MANIFEST
    build_script='build_strict_ycsb_guest.sh'
    run_script='run_strict_ycsb_guest.sh'
    title='严格RR'
    analyze=staticmethod(strict_arm)
    extra_dependencies=(Path(__file__).resolve(),TESTS/'run_expected_ycsb_host.py',TESTS/'run_strict_rr_build_host.py')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args()
    a.label=LABEL;a.phase='build';a.scenarios=[]
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(a);before=resources(profile)
    state=json.loads((SOURCE/'状态.json').read_text());archive=json.loads((SOURCE/'build/阶段归档.json').read_text())
    require(state['当前客机PID'] is None and 'build' in state['已完成'] and archive['完整'] and not archive['客机仍存活'],
        '严格RR库尚未编译验收或正常退出')
    binary=digest(PROJECT/'build-femu/qemu-system-x86_64')
    require(binary==json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'],'严格FEMU版本漂移')
    for s in STAGES:
        for m in MODES:require(not (IMAGE_DIR/f'{LABEL}-{s}-{m}.qcow2').exists(),'拒绝覆盖已有业务镜像')
    if a.check:print(json.dumps(dict(预检通过=True,资源=before),ensure_ascii=False));return
    password=getpass.getpass('严格RR的YCSB隔离客机认证（仅内存）：');output.mkdir()
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=binary,
        设备额外开关='zns_rr_strict=on',模式=list(MODES),阶段=list(STAGES),初始资源=before,
        读取语义='逐页真实NVMe；计数到1024立即同步RR，普通读取锁外重试；仅已触发块自身刷新豁免，其他搬迁计数',
        负载='原8192请求小测及491520键/400000请求短测；种子20261017、单线程、读更新50/50、4KiB，无WAL缓存，不限速',
        不变='原模型核、GC20%、原候选资格、阈值1024、4GiB盘及公共迁移安全规则',
        新边界='同步维护和每页ioctl有额外开销，两组一致；不是旧十秒轮询实验的同语义延续',
        校验='只读数据库、可维护文件系统；业务末态先保存，校验RR另记，不混入业务性能',
        收尾='默认串行一台；成功失败均保存原始日志、哈希归档、正常关机并核验PID/SSH'),ensure_ascii=False,indent=2))
    r=StrictYcsbRun(a,password,output,profile);r.qemu_digest=binary
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        r.build()
        for stage in STAGES:
            for mode in MODES:r.arm(stage,mode)
            result=compare_pair(output,stage,strict_rr=True)
            with (output/(stage+'配对核验.json')).open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
            r.record(阶段=stage+'严格RR配对通过且客机已退出',最近差值=result['模型相对Greedy差值'])
        r.record(阶段='严格RR的YCSB小测及40万请求配对完成，全部客机关闭')
    except BaseException as error:r.record(阶段='严格RR实验停止，不自动重跑',错误=str(error));raise
    finally:r.password=None

if __name__=='__main__':main()
