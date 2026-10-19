#!/usr/bin/env python3
"""新子镜像验证完整空预留周转；先跑Greedy，异常立即采证保留现场。"""
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

LABEL='ycsb-empty-reserve-20261017'
OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'ycsb-reserve-exchange-20261017-build.qcow2'
BUILD_IMAGE=IMAGE_DIR/f'{LABEL}-build.qcow2'
DIRECTORY='/home/femu/rrzc-ycsb-retention-20261017-tests'
TARGET=DIRECTORY+'/empty-reserve-20261017'
RESULT='/home/femu/rrzc-ycsb-results/20261017'
PORT='8096'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/n for n in ('build_ycsb_empty_reserve_guest.sh','run_ycsb_empty_reserve_guest.sh',
    'workloada-zns-nowal.properties','test_single_recovery.cc','test_empty_reserve.cc','probe_ycsb_progress.py')]
FILES += [PROJECT/'research/rrzc/zenfs-empty-reserve.patch',PROJECT/'research/rrzc/model/rrzc_empty_reserve.h']

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
    def scp(self):return ['scp','-q','-o',f'ControlPath={self.socket}','-o','BatchMode=yes','-P',PORT]
    def build_target(self):
        plan=dict(目的='验证公共完整空预留周转能维持长期清理进展，不评价模型收益',
            不变项='设备8GiB、RR4096、GC20%及原空间资格、正常回收预算192MiB、负载655360键及240万次、无WAL、全部RR感知评分与寿命匹配规则',
            修复='正常清理或文件退役重置出空区后，优先以独占标志补完整空预留，再让其余空区进入普通分配',
            行为变化='旧预留部分区优先申请普通活跃令牌并释放尾部给普通池；令牌不足才Finish旧区并单独记录牺牲字节。关闭普通部分区交换，不修改普通寿命匹配或评分',
            验收='原负载完整结束、全键最新值校验、迁移账目闭合、无维护失败；特别检查无空目标时是否使用预留并完成ZC',
            监测='15秒查看；后台分配错误或连续120秒零进展即采证停止监督，保留VM及进程，不自动杀进程或重跑',
            旧实验='原失败客机、日志及50/50与80/20实现完全保留',模式顺序=['构建及单元/普通目录回归','greedy'])
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        source=OUTPUT/'实验源';source.mkdir()
        self.command(['cp',*map(str,FILES),str(Path(__file__).resolve()),str(source)])
        self.start('build')
        parent=json.loads((PROJECT/'research/rrzc/results/ycsb-reserve-exchange-20261017/同库身份.json').read_text())
        before=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(parent))
        require(hashes(before)==parent,'父软件身份不同')
        manifest=source/'parent.software.sha256';manifest.write_text(before)
        self.ssh(f'test ! -e {TARGET} && mkdir {TARGET}')
        self.command(self.scp()+[*map(str,FILES),str(manifest),f'femu@127.0.0.1:{TARGET}/'])
        build=OUTPUT/'build';build.mkdir()
        try:
            self.ssh(f'nohup setsid bash {TARGET}/build_ycsb_empty_reserve_guest.sh < /dev/null > {TARGET}/build-launcher.log 2>&1 &')
            self.record(阶段='仅重新编译公共资格接线并重新链接，执行边界及普通目录回归')
            self.wait_for(f'{TARGET}/build.finished',f'test ! -f {TARGET}/library-build.log || tail -1 {TARGET}/library-build.log; test ! -f {TARGET}/ycsb-build.log || tail -1 {TARGET}/ycsb-build.log',1200)
        finally:self.collect(build,TARGET)
        archive=build/'empty-reserve-20261017'
        require((archive/'build.exit').read_text().strip()=='0' and (archive/'selftest.exit').read_text().strip()=='0','构建或最小回归失败')
        software=hashes((archive/'software.sha256').read_text())
        changes={n for n,d in parent.items() if not n.startswith('/') and software.get(n)!=d}
        require(changes=={'plugin/zenfs/fs/zbd_zenfs.cc','librocksdb_debug.a'},'核心改动超出公共空预留周转范围')
        expected=PROJECT/'build-femu/rrzc/reserve-exchange-work/fs/fs_zenfs.cc'
        require(software['plugin/zenfs/fs/fs_zenfs.cc']==hashlib.sha256(expected.read_bytes()).hexdigest(),'实际接线与冻结副本不符')
        require(software['plugin/zenfs/fs/zbd_zenfs.cc']==hashlib.sha256(
            (PROJECT/'build-femu/rrzc/empty-reserve-work/fs/zbd_zenfs.cc').read_bytes()).hexdigest(),
            '实际完整空预留周转接线与冻结副本不符')
        (OUTPUT/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.shutdown();self.record(阶段='编译和最小回归通过，新构建镜像已正常关闭',当前客机PID=None)
        return software
    def observe_business(self):
        start=changed=time.monotonic();last=None
        while time.monotonic()-start<1800:
            finished=self.ssh(f'test ! -f {TARGET}/run.finished || printf "完成"').strip()=='完成'
            raw=self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py')
            point=json.loads(raw);self.record(阶段=f'{getattr(self,"mode","greedy")}原规模运行与数据核验',最近观测=point)
            with (OUTPUT/'进度.jsonl').open('a') as stream:stream.write(json.dumps(point,ensure_ascii=False)+'\n')
            key=(point['阶段'],point['操作数'])
            if key!=last:last=key;changed=time.monotonic()
            require(not point['错误'],'检测到后台或日志错误，立即采证，保留业务现场')
            if finished:return
            require(time.monotonic()-changed<120,'连续120秒无进展，立即采证并保留现场')
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
            self.root(f'nohup setsid bash {TARGET}/run_ycsb_empty_reserve_guest.sh greedy < /dev/null > {TARGET}/launcher.log 2>&1 &')
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
        result=audit(output/'20261017','greedy',nowal=True,single_recovery=True)
        (output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        require(result['验收通过'],'数据或维护验收未通过')
        self.shutdown();self.record(阶段='单组完整240万次及数据、维护账目验证通过；尚未运行算法对照',当前客机PID=None)
    def arm_existing(self,mode):
        # 只复用已验证的停止构建父镜像，不从业务镜像克隆NAND状态。
        software=json.loads((OUTPUT/'同库身份.json').read_text())
        self.state=json.loads((OUTPUT/'状态.json').read_text())
        self.mode=mode;self.parent_image=BUILD_IMAGE
        self.state['已完成']=[m for m in ('greedy','v1','cost','retention')
            if (OUTPUT/m/'独立核验.json').is_file() and
            json.loads((OUTPUT/m/'独立核验.json').read_text())['验收通过']]
        self.state['模式顺序']=['build',*self.state['已完成'],mode]
        self.command(['cp',str(Path(__file__).resolve()),str(OUTPUT/f'续测入口-{mode}.py')])
        output=OUTPUT/mode;output.mkdir();self.start(mode)
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(software)
        before=self.ssh(query);require(hashes(before)==software,'续测软件身份不同')
        (output/'软件.before.sha256').write_text(before)
        try:
            self.root(f'nohup setsid bash {TARGET}/run_ycsb_empty_reserve_guest.sh {mode} < /dev/null > {TARGET}/launcher.log 2>&1 &')
            self.observe_business()
        finally:
            point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_progress.py'))
            self.collect(output,RESULT);self.collect(output,TARGET)
            (output/'软件.after.sha256').write_text(self.ssh(query))
            log=point.get('维护日志路径')
            require(log and Path(log).parent==Path('/tmp') and Path(log).name.startswith('zenfs_nvme0n1_'),'唯一维护日志未确认')
            self.command(self.scp()+[f'femu@127.0.0.1:{log}',str(output/'现场维护.log')],120)
        require(before==(output/'软件.after.sha256').read_text(),'续测运行中软件改变')
        result=audit(output/'20261017',mode,nowal=True,single_recovery=True)
        (output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        require(result['验收通过'],'续测数据或维护验收失败')
        baseline=json.loads((OUTPUT/'greedy/独立核验.json').read_text())
        require(all(result[k]==baseline[k] for k in ('软件清单SHA256','请求轨迹SHA256','最新值清单SHA256')),
            '续测与Greedy的软件、请求或最新值清单不同')
        self.shutdown();self.state['已完成'].append(mode)
        self.record(阶段=f'{mode}完整240万次、数据与维护核验通过，已正常关闭',当前客机PID=None)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    parser.add_argument('--run-mode',choices=('v1','cost','retention'));args=parser.parse_args()
    if args.run_mode:
        require((OUTPUT/'同库身份.json').is_file() and BUILD_IMAGE.is_file() and not running_parent(BUILD_IMAGE),'没有可复用的停止构建父镜像')
        require((OUTPUT/'greedy/独立核验.json').is_file() and json.loads((OUTPUT/'greedy/独立核验.json').read_text())['验收通过'],'Greedy完整核验尚未通过')
        require(json.loads((OUTPUT/'状态.json').read_text()).get('当前客机PID') is None,'前一组尚未正常结束')
        require(not (OUTPUT/args.run_mode).exists() and not (OUTPUT/f'续测入口-{args.run_mode}.py').exists(),'续测输出已存在')
        with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,'端口仍占用')
    else:preflight()
    if args.check:print('新镜像、父软件、8096和磁盘余量检查通过，未启动实验');return
    password=getpass.getpass('本轮隔离客机认证（仅内存）：')
    if not args.run_mode:OUTPUT.mkdir()
    run=TargetRun(SimpleNamespace(label=LABEL,seconds=600,space_weight=50),password,OUTPUT)
    try:
        if args.run_mode:run.arm_existing(args.run_mode)
        else:run.run_target()
    except BaseException as e:
        run.record(阶段='基础验证失败，保留本轮客机及证据，不启动算法对照',错误=str(e) or type(e).__name__);raise

if __name__=='__main__':main()
