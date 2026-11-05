#!/usr/bin/env python3
"""已验收第二版库只重链接YCSB；先三组小测，再40万请求同范围串行对照。"""
import argparse,getpass,json,signal
from pathlib import Path
import run_joint_host as base
from run_joint_host import PROJECT,IMAGE_DIR,require
from run_window_credit_host import resources,digest
from run_window_ycsb_host import WindowYcsbRun,STAGES
from run_strict_ycsb_host import FILES as OLD_FILES
from run_strict_rr_build_host import StrictBuild
from analyze_terminal_ycsb import MODES,compare_three,analyze_arm

LABEL='joint-terminal-ycsb-v1-20261023'
SOURCE=IMAGE_DIR/'joint-terminal-v2-20261023-results'
PARENT=IMAGE_DIR/'joint-terminal-v2-20261023-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
ROOT=PROJECT/'research/rrzc';TESTS=ROOT/'tests'
FILES=[p for p in OLD_FILES if p.name not in
    ('build_strict_ycsb_guest.sh','run_strict_ycsb_guest.sh','capture_expected_ycsb_guest.py')]
FILES += [TESTS/n for n in ('build_terminal_ycsb_guest.sh','run_terminal_ycsb_guest.sh','capture_space_plan_guest.py')]

class TerminalYcsbRun(WindowYcsbRun,StrictBuild):
    files=FILES
    manifest=MANIFEST
    modes=MODES
    capture_adapter='capture_space_plan_guest.py'
    build_script='build_terminal_ycsb_guest.sh'
    run_script='run_terminal_ycsb_guest.sh'
    title='第二版边界'
    analyze=staticmethod(analyze_arm)
    allowed_build_changes=()
    extra_dependencies=(Path(__file__).resolve(),TESTS/'run_strict_rr_build_host.py',TESTS/'run_strict_ycsb_host.py')

    def start(self,mode):
        super().start(mode)
        if mode=='build':
            free=int(self.ssh("python3 -c 'import os;s=os.statvfs(\"/home/femu/rocksdb\");print(s.f_bavail*s.f_frsize)'"))
            self.record(构建根盘可用字节=free)
            require(free>=6<<30,'根盘不足6GiB，停止，不扩容或删除文件')

    def freeze_extra(self,frozen):
        target=frozen/'模型';target.mkdir()
        for p in (ROOT/'model').glob('*.h'):(target/p.name).write_bytes(p.read_bytes())
        p=TESTS/'test_expected_rr_selector.cc';(frozen/p.name).write_bytes(p.read_bytes())

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true')
    p.add_argument('--label',default=LABEL);a=p.parse_args();a.phase='build';a.scenarios=[]
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(a);before=resources(profile)
    state=json.loads((SOURCE/'状态.json').read_text())
    require(state['当前客机PID'] is None,'源实验尚未关闭')
    for tag in ('build','stable-space','stable-joint','aging-space','aging-joint'):
        require(tag in state['已完成'],'源阶段未完成：'+tag)
        archived=json.loads((SOURCE/tag/'阶段归档.json').read_text())
        require(archived['完整'] and not archived['客机仍存活'],'源阶段未归档退出：'+tag)
    require(json.loads((SOURCE/'平台配置.json').read_text())==profile,'受控平台漂移')
    expected=json.loads(MANIFEST.read_text())
    for name in ('rrzc_terminal_v2.h','rrzc_terminal_selector.h','rrzc_joint_time_selector.h'):
        require(digest(ROOT/'model'/name)==expected['plugin/zenfs/fs/'+name],'实际父模型与源码不同：'+name)
    binary=digest(PROJECT/'build-femu/qemu-system-x86_64')
    require(binary==json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'],'FEMU版本漂移')
    for stage in STAGES:
        for mode in MODES:
            tag=a.label+'-'+stage+'-'+mode
            for path in (IMAGE_DIR/(tag+'.qcow2'),Path('/tmp')/(tag+'.pid'),Path('/tmp')/(tag+'.qmp'),Path('/tmp')/(tag+'-ssh.sock')):
                require(not path.exists(),'拒绝覆盖：'+str(path))
    if a.check:print(json.dumps(dict(只读预检通过=True,资源=before),ensure_ascii=False,indent=2));return
    password=getpass.getpass('同范围第二版YCSB认证（仅内存）：');output.mkdir()
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='不再调参，验证真实YCSB下新增边界成本相对旧RR-aware模型的独立贡献',
        父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=binary,初始资源=before,
        阶段=['build',*[s+'-'+m for s in STAGES for m in MODES]],
        改动='仅重链接新YCSB及独立校验程序；库、ZenFS、模型及旧客户端逐字节不变',
        三组={'greedy':'同扩展候选空间控制，关闭RR与留存评分；不是stock Greedy',
             'space':'旧时间一致RR-aware，边界开关0','model':'相同旧模型预测及空间需求，边界第二版开关1'},
        负载='YCSB-C++公开派生；同种子20261017、单线程、读更新各50%、4KiB字段；8192请求小测后每组400000请求',
        装载='小测8192键，短测491520键；读热集4096键、更新集65536键，逻辑重叠不保证物理同区',
        不变='4GiB/32区/128MiB区/128块每区、严格RR1024、GC20%、固定回收门槛0、共享迁移及目标安全规则',
        口径='不限速、无WAL、无块缓存；全业务维护含装载及Cleanup，独立校验维护另记',
        指标='真实总迁移及RR/ZC拆分、成功Reset及推导块擦除、吞吐、API平均及P99/P99.9/P99.99、前台/Cleanup秒及末态空间',
        验收='新链接身份、四次POSIX完整自测、三组小测后才40万；全键最新值、请求哈希、预测及真实选择独立整数重算',
        收尾='串行一台，约8GiB加16GiB余量；成功失败采证归档哈希核验、正常关机及PID/SSH退出；失败不自动重跑',
        边界='不是32GiB论文全规模或稳态；不预设收益，不把同轮反事实重算当作闭环实测'),ensure_ascii=False,indent=2))
    run=TerminalYcsbRun(a,password,output,profile);run.qemu_digest=binary
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        run.build()
        for stage in STAGES:
            for mode in MODES:run.arm(stage,mode)
            result=compare_three(output,stage)
            (output/(stage+'三组核验.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
            run.record(阶段=stage+'同范围三组完成，已分析且全部客机关闭',最近差值=result['修正版相对旧模型差值'])
        run.record(阶段='第二版同范围YCSB小测及40万三组完成，全部客机关闭')
        print('真实YCSB三组完成并核验：'+str(output),flush=True)
    except BaseException as error:run.record(阶段='第二版YCSB停止，保留原始错误，不自动重跑',错误=str(error));raise
    finally:run.password=None

if __name__=='__main__':main()
