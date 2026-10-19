#!/usr/bin/env python3
"""沿用已编译模型，只新增无WAL绑定配置；短测通过后四组串行，失败保留现场。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace
from run_ycsb_retention_host import YcsbRun
from run_tail_ablation_fixed_pair import running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_rocksdb_guard_pair_host import hashes
from analyze_ycsb_retention import audit,compare

LABEL='ycsb-nowal-20261017'
OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'ycsb-retention-20261017-build.qcow2'
BUILD_IMAGE=IMAGE_DIR/f'{LABEL}-build.qcow2'
DIRECTORY='/home/femu/rrzc-ycsb-retention-20261017-tests'
NOWAL=DIRECTORY+'/nowal-20261017'
RESULT='/home/femu/rrzc-ycsb-results/20261017'
PORT='8094'
MODES=('greedy','v1','cost','retention')
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/n for n in ('ycsb-nowal.patch','build_ycsb_nowal_guest.sh','run_ycsb_nowal_guest.sh',
    'workloada-zns-nowal.properties','workloada-zns-nowal-pilot.properties')]

def properties(path):
    return dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line and not line.startswith('#'))

def parent_identity():
    return json.loads((PROJECT/'research/rrzc/results/ycsb-retention-20261017/同库身份.json').read_text())

def preflight():
    require(not OUTPUT.exists() and Path('/dev/kvm').exists(),'新结果已存在或KVM不可用')
    require(PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR and not running_parent(PARENT),'停止父镜像不符')
    with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,'8094已占用，不触及既有客机')
    for mode in ('build','pilot',*MODES):
        for p in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
                  Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not p.exists(),'新目标已存在：'+str(p))
    for p,minimum in ((IMAGE_DIR,12<<30),(PROJECT,3<<30)):
        fs=os.statvfs(p);require(fs.f_bavail*fs.f_frsize>=minimum,'镜像或归档盘余量不足')
    old=properties(TESTS/'workloada-zns.properties')
    new=properties(TESTS/'workloada-zns-nowal.properties')
    require(new==dict(old,**{'rocksdb.disable_wal':'true'}),'除WAL之外的正式负载参数发生变化')
    require(properties(TESTS/'workloada-zns-nowal-pilot.properties')==dict(new,operationcount='40000'),'短测非完整装载或额外改变参数')
    require(bool(parent_identity()),'缺父镜像软件身份')

class NowalRun(YcsbRun):
    label_prefix=LABEL
    parent_image=PARENT
    port=PORT
    def scp(self):return ['scp','-q','-o',f'ControlPath={self.socket}','-o','BatchMode=yes','-P',PORT]
    def build_nowal(self):
        plan=json.loads((PROJECT/'research/rrzc/results/ycsb-retention-20261017/预注册计划.json').read_text())
        plan.update(标签=LABEL,用户授权='四组统一关闭WAL，沿用此前实验口径',WAL关闭=True,
            对旧失败试次的唯一正式负载变更='rocksdb.disable_wal=true；其他请求、数据库参数及设备参数不变',
            模式顺序=['构建及默认WAL回归','完整655360键装载＋40000次短业务',*MODES],
            持久性边界='无WAL，仅比较维护行为；不评价崩溃或掉电持久性',
            构建范围='仅重编YCSB绑定；ZenFS/FEMU及模型、已有RR与GC路径和预留区管理不修改、不重编',
            短测门槛='同设备完整装载、40000次业务、完整只读最新值校验、维护与选择账目通过后才启动四组',
            日志边界='保留原256MiB单文件保护；若再次触顶，按证据不完整失败，不当作成功',自动重跑=False)
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        source=OUTPUT/'实验源';source.mkdir();(source/'分析').mkdir()
        self.command(['cp',*map(str,FILES),str(Path(__file__).resolve()),str(source)])
        self.command(['cp',*map(str,(PROJECT/'research/rrzc').glob('*.py')),str(source/'分析')])
        self.record(阶段='无WAL试次登记，原失败现场保留',模式顺序=plan['模式顺序'],固定计划=plan)
        build=OUTPUT/'build';build.mkdir();self.start('build')
        parent=parent_identity();query='cd /home/femu/rocksdb; sha256sum '+' '.join(parent)
        actual=self.ssh(query);require(hashes(actual)==parent,'停止构建父镜像的软件不符')
        (build/'父软件.sha256').write_text(actual)
        manifest=source/'parent.software.sha256';manifest.write_text(actual)
        self.ssh(f'test ! -e {NOWAL} && mkdir {NOWAL}')
        self.command(self.scp()+[*map(str,FILES),str(manifest),f'femu@127.0.0.1:{NOWAL}/'])
        try:
            self.ssh(f'nohup setsid bash {NOWAL}/build_ycsb_nowal_guest.sh < /dev/null > {NOWAL}/build-launcher.log 2>&1 &')
            self.record(阶段='只编译无WAL绑定，普通目录核验默认开启及显式关闭WAL')
            self.wait_for(f'{NOWAL}/build.finished',f'test ! -f {NOWAL}/ycsb-build.log || tail -2 {NOWAL}/ycsb-build.log',600)
        finally:self.collect(build,NOWAL)
        archive=build/'nowal-20261017'
        require((archive/'build.exit').read_text().strip()=='0' and (archive/'selftest.exit').read_text().strip()=='0','编译或普通目录回归失败，保留现场')
        software=hashes((archive/'software.sha256').read_text())
        require(all(software.get(n)==digest for n,digest in parent.items() if not n.startswith('/')),'ZenFS库或模型核心身份改变')
        binding=PROJECT/'build-femu/rrzc/ycsb-upstream-20261017/rocksdb/rocksdb_db.cc'
        require(software[DIRECTORY+'/ycsb/rocksdb/rocksdb_db.cc']==hashlib.sha256(binding.read_bytes()).hexdigest(),'绑定补丁实际源与工作副本不同')
        (OUTPUT/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.shutdown();self.record(阶段='无WAL绑定与默认行为回归通过，新构建客机正常关闭',当前客机PID=None)
        return software
    def arm_nowal(self,mode,software):
        output=OUTPUT/mode;output.mkdir();self.parent_image=BUILD_IMAGE;self.start(mode)
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(software)
        before=self.ssh(query);require(hashes(before)==software,'实验软件不同')
        (output/'软件.before.sha256').write_text(before)
        try:
            self.root(f'nohup setsid bash {NOWAL}/run_ycsb_nowal_guest.sh {mode} < /dev/null > {NOWAL}/launcher.log 2>&1 &')
            self.record(阶段=f'{mode}：无WAL完整装载及'+('40000次短业务' if mode=='pilot' else '240万次正式业务'))
            self.wait_for(f'{NOWAL}/run.finished',f'test ! -f {RESULT}/ycsb.log || tail -2 {RESULT}/ycsb.log',1200 if mode=='pilot' else 2400)
        finally:
            self.collect(output,RESULT)
            self.collect(output,NOWAL)
            (output/'软件.after.sha256').write_text(self.ssh(query))
        require(before==(output/'软件.after.sha256').read_text(),'运行中软件改变')
        result=audit(output/'20261017','greedy' if mode=='pilot' else mode,nowal=True,expected_ops=40000 if mode=='pilot' else 2400000)
        (output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        require(result['验收通过'],'无WAL业务或维护验收失败，保留现场，不继续后续组')
        self.shutdown();self.state['已完成'].append(mode)
        self.record(阶段=f'{mode}已核验归档并正常关闭',当前客机PID=None)
    def run_nowal(self):
        software=self.build_nowal()
        self.arm_nowal('pilot',software)
        self.record(阶段='真实ZNS完整装载及短业务通过，开始同库四组无WAL正式对照')
        for mode in MODES:self.arm_nowal(mode,software)
        result=compare(OUTPUT)
        (OUTPUT/'四组对照.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.record(阶段='四组无WAL YCSB-C++完成，结果以核验为准')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    preflight()
    if args.check:print('新目标、父软件、WAL单变量、短测规模、8094及空间只读检查通过');return
    password=getpass.getpass('本轮新客机认证（只驻留内存）：');OUTPUT.mkdir()
    run=NowalRun(SimpleNamespace(label=LABEL,seconds=600,space_weight=50),password,OUTPUT)
    try:run.run_nowal()
    except BaseException as e:
        run.record(阶段='无WAL试次停止，保留原始证据和客机，不自动重跑',错误=str(e) or type(e).__name__);raise

if __name__=='__main__':main()
