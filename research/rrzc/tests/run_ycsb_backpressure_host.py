#!/usr/bin/env python3
"""只提前公共L0写入反压，先普通目录验证，再用原规模重验预热和热读。"""
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
from analyze_ycsb_backpressure import analyze_backpressure

LABEL='ycsb-backpressure-20261018'
OUTPUT=PROJECT/'research/rrzc/results'/LABEL
PARENT=IMAGE_DIR/'ycsb-overlap-20261018-build.qcow2'
BUILD_IMAGE=IMAGE_DIR/f'{LABEL}-build.qcow2'
DIRECTORY='/home/femu/rrzc-ycsb-retention-20261017-tests'
TARGET=DIRECTORY+'/backpressure-20261018'
RESULT='/home/femu/rrzc-ycsb-results/20261017'
PORT='8096'
FAILED=PROJECT/'research/rrzc/results/ycsb-overlap-20261018'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/name for name in ('build_ycsb_backpressure_guest.sh','run_ycsb_overlap_guest.sh',
    'probe_ycsb_overlap.py','probe_ycsb_progress.py','workload-pressure-backpressure-zns.properties',
    'workload-read-backpressure-zns.properties')]


def preflight(retiring=False):
    require(not OUTPUT.exists() and Path('/dev/kvm').exists(),'新输出已存在或KVM不可用')
    require(PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR and not running_parent(PARENT),'父镜像不符或仍在运行')
    if not retiring:
        with socket.socket() as probe:require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,'8096仍被占用')
    for mode in ('build','greedy'):
        for p in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
                  Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not p.exists(),'新目标已存在：'+str(p))
    for p,minimum in ((IMAGE_DIR,12<<30),(PROJECT,3<<30)):
        fs=os.statvfs(p);require(fs.f_bavail*fs.f_frsize>=minimum,'磁盘余量不足')
    if not retiring:
        mem=dict(row.split(':',1) for row in Path('/proc/meminfo').read_text().splitlines())
        require(int(mem['MemAvailable'].split()[0])>=16*1024*1024,'可用内存不足16GiB')


def retire_failed(password):
    # 只处理本轮失败镜像；完整采证后正常关机，不操作任何其他客机。
    state=json.loads((FAILED/'状态.json').read_text());old_label='ycsb-overlap-20261018'
    image=IMAGE_DIR/(old_label+'-greedy.qcow2');pid=state['当前客机PID']
    require(state['当前镜像']==str(image) and state.get('错误')=='出现后台错误，采证保留现场，不自动重跑',
        '不是已经确认的本轮失败现场')
    require(f'file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0'.encode() in
        Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'),'失败客机PID/镜像身份不符')
    output=FAILED/'分配失败诊断/关机前完整';output.mkdir()
    (output/'失败状态原件.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
    run=ReadPilotRun(SimpleNamespace(label=old_label,seconds=1200,space_weight=50),password,output)
    run.state=state;run.label_prefix=old_label;run.port=PORT
    run.socket=f'/tmp/{old_label}-greedy-ssh.sock';run.qmp=Path(f'/tmp/{old_label}-greedy.qmp')
    run.collect(output,RESULT)
    run.command(run.scp()+['femu@127.0.0.1:/tmp/zenfs_nvme0n1_2026-10-18_04:04:59.log',str(output/'维护现场.log')],120)
    (output/'现场校验.sha256').write_text(run.command(['sha256sum',str(output/'维护现场.log'),
        str(output/'20261017/warmup/ycsb.log'),str(output/'20261017/aux/rrzc-ycsb/LOG')]))
    run.record(阶段='失败日志及设备Zone现场已保留，正常关闭本轮失败客机；模拟NAND易失内容不保证关机后恢复')
    run.shutdown();run.record(阶段='本轮失败客机已正常关闭，镜像与日志保留；不是实验成功',当前客机PID=None)
    run.output=FAILED
    run.record(阶段='失败现场完整归档后已正常关闭客机，镜像保留，准备独立反压复测',当前客机PID=None)


class BackpressureRun(ReadPilotRun):
    label_prefix=LABEL
    parent_image=PARENT
    port=PORT

    def observe_business(self):
        start=changed=time.monotonic();last=None
        while time.monotonic()-start<4200:
            finished=self.ssh(f'test ! -f {TARGET}/run.finished || printf "完成"').strip()=='完成'
            point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_overlap.py'))
            self.record(阶段='原规模提前反压复测',最近观测=point)
            with (OUTPUT/'进度.jsonl').open('a') as stream:stream.write(json.dumps(point,ensure_ascii=False)+'\n')
            key=(point['阶段'],point['操作数'])
            if key!=last:last=key;changed=time.monotonic()
            require(not point['错误'],'后台错误，采证保留现场，不自动重跑')
            if finished:return
            require(time.monotonic()-changed<180,'180秒无请求进展，采证保留现场')
            time.sleep(15)
        raise RuntimeError('超过4200秒观察上限，采证保留现场')

    def build_configuration(self):
        plan=dict(目的='修复预热期间压实追赶不足导致的分配失败，验证原规模能否完成；不是模型收益对照',
            唯一配置变化='两阶段统一L0减速20→4、暂停36→8，原压实触发2不变；使用现有绑定属性，不修改二进制',
            不变项='FEMU、ZenFS、RocksDB及YCSB代码/二进制；8GiB、RR4096、GC20%、回收192MiB、额外16MiB、全部模型和恢复开关',
            负载='仍655360键，120万uniform全更新上限2000次/秒，再60万latest的95%读5%更新上限1000次/秒，同次FEMU启动',
            测试='先普通目录双次真实SST/压实、实际Options4/8、两阶段请求清单及全键校验，再原规模设备复测',
            验收='必须越过1036952旧卡点并完成120万预热、60万热读及全键校验；次数与机会不足仍如实报告',
            边界='公共非stock实验配置；后续所有机制组须统一使用，不能与旧无反压结果直接归因于模型',
            监测='15秒观察，180秒无请求进展或后台错误采证；观察上限4200秒，不自动重跑')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        source=OUTPUT/'实验源';source.mkdir()
        self.command(['cp',*map(str,FILES),str(Path(__file__).resolve()),str(source)])
        analysis=OUTPUT/'分析源';analysis.mkdir()
        self.command(['cp',*map(str,(PROJECT/'research/rrzc').glob('*.py')),str(analysis)])
        self.start('build')
        parent=json.loads((FAILED/'同库身份.json').read_text())
        before=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(parent))
        require(hashes(before)==parent,'停止父镜像软件身份不同')
        manifest=source/'parent.software.sha256';manifest.write_text(before)
        self.ssh(f'test ! -e {TARGET} && mkdir {TARGET}')
        self.command(self.scp()+[*map(str,FILES),str(manifest),f'femu@127.0.0.1:{TARGET}/'])
        output=OUTPUT/'build';output.mkdir()
        try:
            self.ssh(f'nohup setsid bash {TARGET}/build_ycsb_backpressure_guest.sh < /dev/null > {TARGET}/build-launcher.log 2>&1 &')
            self.record(阶段='同二进制普通目录双次压实、实际反压配置及跨阶段全键校验')
            self.wait_for(f'{TARGET}/build.finished',f'test ! -f {TARGET}/build-launcher.log || tail -1 {TARGET}/build-launcher.log',600)
        finally:self.collect(output,TARGET)
        archive=output/'backpressure-20261018'
        require((archive/'build.exit').read_text().strip()=='0' and (archive/'selftest.exit').read_text().strip()=='0','反压配置最小验证失败')
        software=hashes((archive/'software.sha256').read_text())
        require(all(software.get(name)==digest for name,digest in parent.items()),'核心软件或旧冻结脚本发生变化')
        (OUTPUT/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.shutdown();self.record(阶段='最小压实/反压/数据校验通过，配置镜像正常关闭',当前客机PID=None)
        return software

    def run_target(self):
        software=self.build_configuration();self.parent_image=BUILD_IMAGE;self.start('greedy')
        output=OUTPUT/'greedy';output.mkdir()
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(software)
        before=self.ssh(query);require(hashes(before)==software,'业务前身份不同')
        (output/'软件.before.sha256').write_text(before)
        try:
            self.root(f'nohup setsid env RRZC_RUN_TARGET={TARGET} bash {TARGET}/run_ycsb_overlap_guest.sh greedy < /dev/null > {TARGET}/launcher.log 2>&1 &')
            self.observe_business()
        finally:
            point=json.loads(self.ssh(f'python3 {TARGET}/probe_ycsb_overlap.py'))
            self.collect(output,RESULT);self.collect(output,TARGET)
            (output/'软件.after.sha256').write_text(self.ssh(query))
            log=point.get('维护日志路径')
            if log:
                require(Path(log).parent==Path('/tmp') and Path(log).name.startswith('zenfs_nvme0n1_'),'现场日志路径不符')
                self.command(self.scp()+[f'femu@127.0.0.1:{log}',str(output/'现场维护.log')],120)
        require(before==(output/'软件.after.sha256').read_text(),'运行期间软件改变')
        result=analyze_backpressure(output/'20261017')
        (output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        require(result['验收通过'],'反压原规模复测或数据/维护审计失败')
        self.shutdown();self.record(阶段='原规模120万预热＋60万热读及全键审计通过，客机正常关闭',当前客机PID=None,
            已完成=['原规模提前反压复测'],热读RR=result['热读阶段']['RR重置'],热读ZC=result['热读阶段']['ZC重置'],
            具备进入机制配对条件=result['具备进入机制配对条件'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    parser.add_argument('--retire-failed',action='store_true',help='先完整归档并正常关闭精确旧失败客机，不删除镜像')
    args=parser.parse_args();preflight(retiring=args.retire_failed)
    if args.check:print('新镜像及停止父镜像前置检查通过，未启动或关闭客机');return
    password=getpass.getpass('本轮修复及隔离复测认证（仅内存）：')
    if args.retire_failed:retire_failed(password)
    preflight();OUTPUT.mkdir()
    run=BackpressureRun(SimpleNamespace(label=LABEL,seconds=1200,space_weight=50),password,OUTPUT)
    run.state.update(预热更新数=1200000,热读业务请求数=600000,观察上限秒数=4200)
    try:run.run_target()
    except BaseException as error:
        run.record(阶段='反压修正验证失败，保留真实现场',错误=str(error) or type(error).__name__);raise

if __name__=='__main__':main()
