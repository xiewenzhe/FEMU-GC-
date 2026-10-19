#!/usr/bin/env python3
"""近期对齐及RR可触发性短测，不自动进入算法长测。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from run_ycsb_retention_host import YcsbRun
from run_tail_ablation_fixed_pair import running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_rocksdb_guard_pair_host import hashes
from analyze_ycsb_retention import audit

LABEL='ycsb-read-pilot-20261018'
OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'ycsb-space-emergency-20261018-build.qcow2'
BUILD_IMAGE=IMAGE_DIR/f'{LABEL}-build.qcow2'
DIRECTORY='/home/femu/rrzc-ycsb-retention-20261017-tests'
TARGET=DIRECTORY+'/read-pilot-20261018'
RESULT='/home/femu/rrzc-ycsb-results/20261017'
PORT='8096'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/n for n in ('build_ycsb_read_pilot_guest.sh','run_ycsb_read_pilot_guest.sh',
    'workload-read-latest-zns.properties','test_single_recovery.cc','test_alloc_retry.cc',
    'test_rrzc_v1_growth.cc','probe_ycsb_progress.py')]
FILES += [PROJECT/'research/rrzc/zenfs-align-recent.patch']

def preflight():
    require(not OUTPUT.exists() and Path('/dev/kvm').exists(),'输出已存在或KVM不可用')
    require(PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR and not running_parent(PARENT),'父镜像不符或正在使用')
    with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,'8096被占用')
    for mode in ('build','greedy'):
        for p in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
                  Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not p.exists(),'新目标已经存在：'+str(p))
    for p,minimum in ((IMAGE_DIR,12<<30),(PROJECT,3<<30)):
        fs=os.statvfs(p);require(fs.f_bavail*fs.f_frsize>=minimum,'磁盘余量不足')

class TargetRun(YcsbRun):
    label_prefix=LABEL
    parent_image=PARENT
    port=PORT
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.state['模式顺序']=['build','greedy']
        self.state['业务请求数']=600000
        self.state['观察上限秒数']=1800
    def scp(self):return ['scp','-q','-o',f'ControlPath={self.socket}','-o','BatchMode=yes','-P',PORT]
    def shutdown(self):
        try:return super().shutdown()
        except RuntimeError as error:
            if str(error)!='新客机未正常退出，不强制终止或启动下一组':raise
        pid=self.state['当前客机PID'];proc=Path(f'/proc/{pid}')
        if not proc.exists():return
        image=Path(self.state['当前镜像'])
        require(image.parent==IMAGE_DIR and image.name.startswith(self.label_prefix+'-') and
            f'file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0'.encode() in
            (proc/'cmdline').read_bytes().split(b'\0'),'正常关机回退的PID或镜像不符')
        self.record(阶段='本轮ACPI未退出，改用精确客机内正常poweroff，不强制断电')
        try:self.root('systemctl poweroff',15)
        except RuntimeError as error:self.record(正常关机连接反馈=str(error))
        for _ in range(60):
            if not proc.exists():return
            time.sleep(1)
        raise RuntimeError('本轮客机内正常关机仍未退出，保留现场，不强制断电')
    def build_target(self):
        plan=dict(目的='确认真实RR可触发性与同块对齐，不宣称稳态或算法收益',
            不变项='8GiB、655360键、4KiB字段、RR4096、GC20%及全部公共恢复开关、实际Greedy及模型参数',
            代码='只修正p99提取与默认关闭的近期对齐；不跨中间换块、Reset或计数回退，不跳过合法零增长点',
            负载='YCSB-C++现有latest生成器、有序20位补零键、95%读5%更新，600000次，上限1000次每秒，无WAL，无块缓存；不是原始Workload B',
            假设='降低更新频率且减少随机键打散，让持久SST积累读数；实际空间集中度须看日志，不预先保证',
            判定='正确性和RR触发分开，RR为0也保留真实结果，不自动降阈值或重跑，不启动四组长测',
            监测='15秒查看，最多1800秒；后台错误或180秒无进展采证停止并保留现场',
            旧实验='已成功基线、旧50/50及80/20不覆盖',模式顺序=['真实编译及同块/五次普通目录回归','60万次Greedy读密集短测及全键校验'])
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        source=OUTPUT/'实验源';source.mkdir()
        self.command(['cp',*map(str,FILES),str(Path(__file__).resolve()),str(source)])
        self.start('build')
        parent=json.loads((PROJECT/'research/rrzc/results/ycsb-space-emergency-20261018/同库身份.json').read_text())
        before=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(parent))
        require(hashes(before)==parent,'父软件身份不同')
        manifest=source/'parent.software.sha256';manifest.write_text(before)
        self.ssh(f'test ! -e {TARGET} && mkdir {TARGET}')
        self.command(self.scp()+[*map(str,FILES),str(manifest),f'femu@127.0.0.1:{TARGET}/'])
        build=OUTPUT/'build';build.mkdir()
        try:
            self.ssh(f'nohup setsid bash {TARGET}/build_ycsb_read_pilot_guest.sh < /dev/null > {TARGET}/build-launcher.log 2>&1 &')
            self.record(阶段='编译同块对齐与公开latest普通目录回归')
            self.wait_for(f'{TARGET}/build.finished',f'test ! -f {TARGET}/library-build.log || tail -1 {TARGET}/library-build.log; test ! -f {TARGET}/ycsb-build.log || tail -1 {TARGET}/ycsb-build.log',1200)
        finally:self.collect(build,TARGET)
        archive=build/'read-pilot-20261018'
        require((archive/'build.exit').read_text().strip()=='0' and (archive/'selftest.exit').read_text().strip()=='0','构建或最小回归失败')
        software=hashes((archive/'software.sha256').read_text())
        changes={n for n,d in parent.items() if not n.startswith('/') and software.get(n)!=d}
        require(changes=={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_v1_growth.h','librocksdb_debug.a'},'核心改动超出近期对齐范围')
        expected=PROJECT/'build-femu/rrzc/aligned-read-work/fs/fs_zenfs.cc'
        require(software['plugin/zenfs/fs/fs_zenfs.cc']==hashlib.sha256(expected.read_bytes()).hexdigest(),'实际接线与冻结副本不符')
        require(software['plugin/zenfs/fs/zbd_zenfs.cc']==hashlib.sha256(
            (PROJECT/'build-femu/rrzc/space-emergency-work/fs/zbd_zenfs.cc').read_bytes()).hexdigest(),
            '实际公共分配FIFO接线与冻结副本不符')
        for file in ('zbd_zenfs.h','rrzc_space_emergency.h'):
            expected=(PROJECT/'build-femu/rrzc/space-emergency-work/fs'/file) if file=='zbd_zenfs.h' else (PROJECT/'research/rrzc/model'/file)
            require(software['plugin/zenfs/fs/'+file]==hashlib.sha256(expected.read_bytes()).hexdigest(),'实际头文件与冻结副本不符')
        require(software['plugin/zenfs/fs/rrzc_v1_growth.h']==hashlib.sha256(
            (PROJECT/'research/rrzc/model/rrzc_v1_growth.h').read_bytes()).hexdigest(),'实际对齐帮助头不符')
        (OUTPUT/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.shutdown();self.record(阶段='编译和最小回归通过，新构建镜像已正常关闭',当前客机PID=None)
        return software
    def observe_business(self):
        start=changed=time.monotonic();last=None
        while time.monotonic()-start<1800:
            finished=self.ssh(f'test ! -f {TARGET}/run.finished || printf "完成"').strip()=='完成'
            raw=self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py')
            point=json.loads(raw);self.record(阶段=f'{getattr(self,"mode","greedy")}读密集60万次与数据核验',最近观测=point)
            with (OUTPUT/'进度.jsonl').open('a') as stream:stream.write(json.dumps(point,ensure_ascii=False)+'\n')
            key=(point['阶段'],point['操作数'])
            if key!=last:last=key;changed=time.monotonic()
            require(not point['错误'],'检测到后台或日志错误，立即采证，保留业务现场')
            if finished:return
            require(time.monotonic()-changed<180,'连续180秒无进展，立即采证并保留现场')
            time.sleep(15)
        raise RuntimeError('基础验证超时，保留现场')
    def run_target(self):
        software=self.build_target()
        self.parent_image=BUILD_IMAGE;self.start('greedy')
        output=OUTPUT/'greedy';output.mkdir()
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(software)
        identity=self.ssh(query);require(hashes(identity)==software,'业务前软件身份不同')
        (output/'软件.before.sha256').write_text(identity)
        try:
            self.root(f'nohup setsid bash {TARGET}/run_ycsb_read_pilot_guest.sh greedy < /dev/null > {TARGET}/launcher.log 2>&1 &')
            self.observe_business()
        finally:
            # 包装器失败前可能尚未复制维护日志；按唯一新增日志规则主动采证。
            point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
            self.collect(output,RESULT)
            self.collect(output,TARGET)
            (output/'软件.after.sha256').write_text(self.ssh(query))
            log=point.get('维护日志路径')
            require(log and Path(log).parent==Path('/tmp') and Path(log).name.startswith('zenfs_nvme0n1_'),
                '未确认唯一业务维护日志，不能猜测归档')
            self.command(self.scp()+[f'femu@127.0.0.1:{log}',str(output/'现场维护.log')],120)
        require(identity==(output/'软件.after.sha256').read_text(),'运行期间软件改变')
        result=audit(output/'20261017','greedy',nowal=True,expected_ops=600000,single_recovery=True,space_emergency=True)
        result['RR可触发性已观察']=result['RR重置']>0
        result['实验定位']='读密集派生负载可触发性短测，不是稳态或收益对照'
        (output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        require(result['验收通过'],'数据或维护验收未通过')
        self.shutdown();self.record(阶段='60万次及全键校验通过，客机已正常关闭，是否有RR以计数为准',当前客机PID=None,RR重置=result['RR重置'],ZC重置=result['ZC重置'],已完成=['greedy读密集短测'])

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();preflight()
    if args.check:print('新隔离镜像、停止父镜像与资源检查通过，未启动');return
    password=getpass.getpass('本轮隔离客机认证（仅内存）：');OUTPUT.mkdir()
    run=TargetRun(SimpleNamespace(label=LABEL,seconds=600,space_weight=50),password,OUTPUT)
    try:run.run_target()
    except BaseException as error:
        run.record(阶段='读密集RR可触发性验证失败，保留本轮现场',错误=str(error) or type(error).__name__);raise

if __name__=='__main__':main()
