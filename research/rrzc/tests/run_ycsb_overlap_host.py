#!/usr/bin/env python3
"""构建及运行一次空间预热＋热读短测；没有机会也不自动改参数或重跑。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from run_ycsb_read_pilot_host import TargetRun as ReadPilotRun
from run_tail_ablation_fixed_pair import running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_rocksdb_guard_pair_host import hashes
from analyze_ycsb_overlap import analyze

LABEL='ycsb-overlap-20261018'
OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'ycsb-read-pilot-20261018-build.qcow2'
BUILD_IMAGE=IMAGE_DIR/f'{LABEL}-build.qcow2'
DIRECTORY='/home/femu/rrzc-ycsb-retention-20261017-tests'
TARGET=DIRECTORY+'/overlap-20261018'
RESULT='/home/femu/rrzc-ycsb-results/20261017'
PORT='8096'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/name for name in ('build_ycsb_overlap_guest.sh','run_ycsb_overlap_guest.sh',
    'probe_ycsb_overlap.py','probe_ycsb_progress.py','workload-pressure-warmup-zns.properties',
    'workload-read-latest-zns.properties','rrzc_ycsb_audit.h','test_ycsb_audit_import.cc')]


def preflight():
    require(not OUTPUT.exists() and Path('/dev/kvm').exists(),'输出已存在或KVM不可用')
    require(PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR and not running_parent(PARENT),'父镜像不符或仍在运行')
    with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,'8096已被占用')
    for mode in ('build','greedy'):
        for p in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
                  Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not p.exists(),'新目标已存在：'+str(p))
    for p,minimum in ((IMAGE_DIR,12<<30),(PROJECT,3<<30)):
        fs=os.statvfs(p);require(fs.f_bavail*fs.f_frsize>=minimum,'磁盘余量不足')
    mem=dict(row.split(':',1) for row in Path('/proc/meminfo').read_text().splitlines())
    require(int(mem['MemAvailable'].split()[0])>=16*1024*1024,'可用内存不足16GiB，不新增客机')


class OverlapRun(ReadPilotRun):
    label_prefix=LABEL
    parent_image=PARENT
    port=PORT

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.state.update(预热更新数=1200000,热读业务请求数=600000,观察上限秒数=2700)

    def build_target(self,repair=False):
        plan=dict(目的='让临RR物理块所在Zone在真实空间回收轮次同时具备正常候选资格，先验证机会，不直接启动四组长测',
            不变项='8GiB、655360键、4KiB字段、RR4096、GC20%、回收预算192MiB、额外16MiB、原选择公式及公共恢复开关',
            代码='只给请求校验增加默认关闭的清单继承；FEMU、ZenFS和RocksDB静态库与上一轮构建逐字节相同',
            阶段='同次设备启动：全量装载＋120万次uniform全更新、上限2000次每秒；正常关闭再打开数据库，60万次latest的95%读5%更新、上限1000次每秒',
            数据连续='相同ordered补零键，第二进程严格导入第一进程最近值清单；导入键不算装载，阶段间不做全键读取；最终独立只读全键校验',
            边界='公开YCSB-C++生成器的分阶段派生负载，不是原始标准Workload；重开进程会重建主机采样历史，设备RC与Reset代际不清零',
            假设='均匀更新制造失效与空间压力，随后持久热读；不保证重叠，须用正常GC候选与同块ETA日志确认',
            验收='预热和热读分别核验RR、ZC、搬运、延迟；热读阶段RR及ZC均非零且有非Greedy临RR合格候选和影子建议，才考虑机制配对',
            监测='15秒观察，180秒无进展或后台错误采证保留，观察上限2700秒；不自动降阈值、重跑或清理旧镜像')
        if not repair:(OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        source=OUTPUT/('编译修正源' if repair else '实验源');source.mkdir()
        self.command(['cp',*map(str,FILES),str(Path(__file__).resolve()),str(source)])
        if repair:
            self.state=json.loads((OUTPUT/'状态.json').read_text())
            pid=self.state['当前客机PID']
            require(self.state['当前镜像']==str(BUILD_IMAGE) and self.state.get('错误')=='构建或跨阶段回归失败',
                '不是本轮首次构建失败，拒绝套用编译修正')
            require(f'file={BUILD_IMAGE},if=none,aio=native,cache=none,format=qcow2,id=hd0'.encode() in
                Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'),'修正PID与本轮构建镜像不符')
            self.socket=f'/tmp/{LABEL}-build-ssh.sock';self.qmp=Path(f'/tmp/{LABEL}-build.qmp')
            self.state['监督进程PID']=os.getpid()
        else:
            analysis=OUTPUT/'分析源';analysis.mkdir()
            self.command(['cp',*map(str,(PROJECT/'research/rrzc').glob('*.py')),str(analysis)])
            self.start('build')
        parent=json.loads((PROJECT/'research/rrzc/results/ycsb-read-pilot-20261018/同库身份.json').read_text())
        before=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(parent))
        require(hashes(before)==parent,'停止父镜像软件身份不符')
        manifest=source/'parent.software.sha256';manifest.write_text(before)
        if repair:
            self.ssh(f'set -e; test ! -e {RESULT}; test -f {TARGET}/build.finished; '
                f'test ! -e {TARGET}-first-build-failure; mv {TARGET} {TARGET}-first-build-failure; mkdir {TARGET}')
        else:self.ssh(f'test ! -e {TARGET} && mkdir {TARGET}')
        self.command(self.scp()+[*map(str,FILES),str(manifest),f'femu@127.0.0.1:{TARGET}/'])
        output=OUTPUT/('build-修正' if repair else 'build');output.mkdir()
        try:
            self.ssh(f'nohup setsid bash {TARGET}/build_ycsb_overlap_guest.sh < /dev/null > {TARGET}/build-launcher.log 2>&1 &')
            self.record(阶段='只编译请求审计，普通目录双次跨阶段及默认入口回归',错误=None)
            self.wait_for(f'{TARGET}/build.finished',f'test ! -f {TARGET}/ycsb-build.log || tail -1 {TARGET}/ycsb-build.log',600)
        finally:self.collect(output,TARGET)
        archive=output/'overlap-20261018'
        require((archive/'build.exit').read_text().strip()=='0' and (archive/'selftest.exit').read_text().strip()=='0','构建或跨阶段回归失败')
        software=hashes((archive/'software.sha256').read_text())
        changed={name for name,value in parent.items() if software.get(name)!=value}
        require(changed=={DIRECTORY+'/ycsb/ycsb'},'变化超出请求审计二进制范围')
        (OUTPUT/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.shutdown();self.record(阶段='请求审计编译及跨阶段校验通过，构建镜像已正常关闭',当前客机PID=None)
        return software

    def observe_business(self):
        start=changed=time.monotonic();last=None
        while time.monotonic()-start<2700:
            finished=self.ssh(f'test ! -f {TARGET}/run.finished || printf "完成"').strip()=='完成'
            point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_overlap.py'))
            self.record(阶段='空间预热＋热读重叠验证',最近观测=point)
            with (OUTPUT/'进度.jsonl').open('a') as stream:stream.write(json.dumps(point,ensure_ascii=False)+'\n')
            key=(point['阶段'],point['操作数'])
            if key!=last:last=key;changed=time.monotonic()
            require(not point['错误'],'出现后台错误，采证保留现场，不自动重跑')
            if finished:return
            require(time.monotonic()-changed<180,'180秒无业务进展，采证保留现场')
            time.sleep(15)
        raise RuntimeError('超过2700秒观察上限，采证保留现场')

    def run_target(self,repair=False):
        software=self.build_target(repair=repair)
        self.parent_image=BUILD_IMAGE;self.start('greedy')
        output=OUTPUT/'greedy';output.mkdir()
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(software)
        before=self.ssh(query);require(hashes(before)==software,'业务前软件身份不符')
        (output/'软件.before.sha256').write_text(before)
        try:
            self.root(f'nohup setsid bash {TARGET}/run_ycsb_overlap_guest.sh greedy < /dev/null > {TARGET}/launcher.log 2>&1 &')
            self.observe_business()
        finally:
            self.collect(output,RESULT)
            self.collect(output,TARGET)
            (output/'软件.after.sha256').write_text(self.ssh(query))
        require(before==(output/'软件.after.sha256').read_text(),'运行期间软件改变')
        result=analyze(output/'20261017')
        (output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        require(result['验收通过'],'数据或维护未通过验收，保留现场')
        self.shutdown();self.record(阶段='预热及热读通过校验，正常关闭；是否有机制机会以分阶段报告为准',当前客机PID=None,
            已完成=['空间预热＋热读短测'],热读RR=result['热读阶段']['RR重置'],热读ZC=result['热读阶段']['ZC重置'],
            具备进入机制配对条件=result['具备进入机制配对条件'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    parser.add_argument('--repair-build',action='store_true',help='只修正本轮尚未使用ZNS的首次构建失败，保留原始失败目录')
    args=parser.parse_args()
    if args.repair_build:require(OUTPUT.is_dir() and not (OUTPUT/'greedy').exists(),'修正对象不存在或已执行设备业务')
    else:preflight()
    if args.check:print('停止父镜像、新目标、磁盘、内存及端口检查通过，未启动');return
    password=getpass.getpass('本轮隔离客机认证（仅内存）：')
    if not args.repair_build:OUTPUT.mkdir()
    run=OverlapRun(SimpleNamespace(label=LABEL,seconds=1200,space_weight=50),password,OUTPUT)
    try:run.run_target(repair=args.repair_build)
    except BaseException as error:
        run.record(阶段='空间与热读重叠验证失败，保留现场',错误=str(error) or type(error).__name__);raise

if __name__=='__main__':main()
