#!/usr/bin/env python3
"""时序成本与两秒同块增长：独立编译并串行验收同负载YCSB，不覆盖旧版本。"""
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

LABEL='joint-deadline-v2-20261022'
SOURCE=IMAGE_DIR/'joint-space-plan-v2-20261022-results'
PARENT=IMAGE_DIR/'joint-space-plan-v2-20261022-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
ROOT=PROJECT/'research/rrzc';TESTS=ROOT/'tests'
FILES=[p for p in OLD_FILES if p.name not in ('build_strict_ycsb_guest.sh','run_strict_ycsb_guest.sh',
    'capture_expected_ycsb_guest.py')]
FILES += [TESTS/n for n in ('build_deadline_guest.sh','run_deadline_guest.sh',
    'capture_space_plan_guest.py','test_deadline_selector.cc')]
FILES += [ROOT/'model/rrzc_deadline_selector.h',ROOT/'zenfs-deadline.patch']

def versioned_arm(root,stage,mode):
    return analyze_arm(root,stage,mode,version=3)

class SpaceRun(WindowYcsbRun,StrictBuild):
    files=FILES
    manifest=MANIFEST
    modes=MODES
    capture_adapter='capture_space_plan_guest.py'
    build_script='build_deadline_guest.sh'
    run_script='run_deadline_guest.sh'
    title='时序模型'
    analyze=staticmethod(versioned_arm)
    allowed_build_changes=('plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','librocksdb_debug.a')
    extra_dependencies=(Path(__file__).resolve(),TESTS/'run_strict_rr_build_host.py',TESTS/'run_strict_ycsb_host.py')

    def __init__(self,args,*rest):
        self.extend_build_root=getattr(args,'extend_build_root',False)
        super().__init__(args,*rest)

    def start(self,mode):
        super().start(mode)
        if mode!='build':return
        free=int(self.ssh("python3 -c 'import os;s=os.statvfs(\"/home/femu/rocksdb\");print(s.f_bavail*s.f_frsize)'"))
        self.record(构建根盘扩容前可用字节=free)
        if free<6<<30:
            require(self.extend_build_root,'客机根盘不足6GiB，停止；须显式允许仅新构建子镜像根卷扩容')
            # 路径及文件系统必须与只读诊断完全一致；不扩虚拟SSD，不改父镜像或分区。
            command='''set -euo pipefail
test "$(findmnt -n -o SOURCE,FSTYPE /)" = '/dev/mapper/ubuntu--vg-ubuntu--lv ext4'
vgs --units b --nosuffix -o vg_free --noheadings ubuntu-vg | awk '{if ($1<8589934592) exit 1}'
df -B1 /
lvextend -L +8G /dev/mapper/ubuntu--vg-ubuntu--lv
resize2fs /dev/mapper/ubuntu--vg-ubuntu--lv
df -B1 /
'''
            result=self.root(command,120)
            (self.output/'构建子镜像根卷扩容.log').write_text(result)
        after=int(self.ssh("python3 -c 'import os;s=os.statvfs(\"/home/femu/rocksdb\");print(s.f_bavail*s.f_frsize)'"))
        require(after>=6<<30,'扩容后客机可用空间仍不足，停止，不删除文件')
        self.record(构建根盘可用字节=after,根盘处理='仅新子镜像根卷；原镜像、NAND几何及研究配置不变')

    def freeze_extra(self,frozen):
        target=frozen/'模型';target.mkdir()
        for p in (ROOT/'model').glob('*.h'):(target/p.name).write_bytes(p.read_bytes())
        # raw预测核重算器仍从相同冻结版本重建，不能只校验新方案自己的输出。
        p=TESTS/'test_expected_rr_selector.cc';(frozen/p.name).write_bytes(p.read_bytes())

    def build(self):
        super().build()
        manifest=json.loads((self.output/'同库身份.json').read_text())
        require(manifest['plugin/zenfs/fs/rrzc_deadline_selector.h']==digest(self.output/'源码/rrzc_deadline_selector.h'),
            '实际空间方案核与冻结源码不同')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true')
    p.add_argument('--label',default=LABEL)
    p.add_argument('--extend-build-root',action='store_true',help='仅新构建子镜像根卷空间不足时，使用卷组空闲空间扩展8GiB；不改模拟SSD')
    a=p.parse_args();a.phase='build';a.scenarios=[]
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
    password=getpass.getpass('时序模型YCSB隔离实验认证（仅内存）：');output.mkdir()
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    plan=dict(父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=binary,初始资源=before,
        新子镜像根卷='空间不足6GiB时用卷组已确认空闲扩展8GiB' if a.extend_build_root else '禁止自动扩容；不足则停止',
        阶段=['build',*[s+'-'+m for s in STAGES for m in MODES]],
        同分修正='预计总复制相同时，优先减少下一检查前的强制RR复制压力，再比较当前首区复制；不先固定Greedy',
        改动='仅显式开启空间方案时：真实字节缺口为零且无分配等待可不选区；每候选作首区计算补足当前空间所需总复制及未处理候选预计RR复制',
        近似='补充区按当前复制/回收比排列；补充ZC在RR未发生分支才收费，RR可能替代补充ZC；未来RR空间只报告，不抵当前缺口；不预测多个RR及目标再刷新',
        三组={'greedy':'原动态候选+原Greedy', 'space':'扩展满区正回收候选+空间方案，无RR抵扣',
            'model':'同space候选与需求+至少两秒同块增长、原支撑窗口及留存；未执行首区的RR成本和互斥补充ZC进入总成本'},
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
            result=compare_three(output,s,version=3)
            (output/(s+'三组核验.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
            r.record(阶段=s+'三组完成，已分析且全部客机关闭',最近差值=result['相对原Greedy差值'])
        r.record(阶段='时序模型YCSB小测及40万三组完成，全部客机关闭')
    except BaseException as e:r.record(阶段='空间方案实验停止，保留原始错误且不自动重跑',错误=str(e));raise
    finally:r.password=None

if __name__=='__main__':main()
