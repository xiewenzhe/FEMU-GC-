#!/usr/bin/env python3
"""统一模型隔离构建、功能短测与串行YCSB入口；全部阶段归档正常关机。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace
from run_benefit_device_host import DeviceRun
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_tail_ablation_fixed_pair import running_parent
from run_rocksdb_guard_pair_host import hashes

LABEL='direct-v2-20261020'
OUTPUT=IMAGE_DIR/(LABEL+'-results')
PARENT=IMAGE_DIR/'benefit-device-20261019-build.qcow2'
BUILD=IMAGE_DIR/(LABEL+'-build.qcow2')
OLD=IMAGE_DIR/'benefit-device-20261019-results'
DIRECTORY='/home/femu/rrzc-ycsb-retention-20261017-tests'
TARGET=DIRECTORY+'/'+LABEL
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/n for n in ('build_direct_guest.sh','run_direct_smoke_guest.sh','benefit_device_smoke.cc',
    'v1_execution_smoke_test.cc','reset_generation_probe.cc','direct_spare_smoke.cc','content_guard_smoke.cc','test_direct_selector.cc','workload-locality-wide-zns.properties')]
FILES += [PROJECT/'research/rrzc/model'/n for n in ('rrzc_direct_selector.h','rrzc_direct_zenfs.inc','rrzc_zone_content.h','rrzc_weighted_selector.h')]
FILES += [PROJECT/'research/rrzc'/n for n in ('zenfs-direct-selector.patch','zenfs-direct-spare-observe.patch','zenfs-content-guard.patch')]


def resources():
    mem=dict(s.split(':',1) for s in Path('/proc/meminfo').read_text().splitlines())
    require(int(mem['MemAvailable'].split()[0])>=16*1024*1024,'可用内存不足16GiB')
    st=os.statvfs(IMAGE_DIR);require(st.f_bavail*st.f_frsize>=24<<30,'data盘余量不足24GiB')


def preflight(phase):
    require(Path('/dev/kvm').exists(),'KVM不可用')
    parent=PARENT if phase=='build' else BUILD
    require(parent.is_file() and not running_parent(parent),'父镜像不存在或仍在运行')
    require(not OUTPUT.exists() if phase=='build' else (OUTPUT/'同库身份.json').is_file(),'结果状态不符')
    if phase=='pair':
        require(json.loads((OUTPUT/'smoke/独立核验.json').read_text())['验收通过'] and
            json.loads((OUTPUT/'spare/独立核验.json').read_text())['验收通过'] and
            json.loads((OUTPUT/'content/独立核验.json').read_text())['验收通过'] and
            json.loads((OUTPUT/'状态.json').read_text()).get('当前客机PID') is None,'设备短测未通过或客机未关闭')
    modes=('build','smoke','spare','content') if phase=='build' else ('greedy','direct')
    with socket.socket() as p:require(p.connect_ex(('127.0.0.1',8096))!=0,'8096端口被占用')
    for mode in modes:
        for p in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
            Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not p.exists(),'目标已存在，拒绝覆盖：'+str(p))
    resources()


class DirectRun(DeviceRun):
    label_prefix=LABEL
    parent_image=PARENT
    port='8096'

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.state.update(模式顺序=['build','smoke','spare','content','greedy','direct'],已完成=[])

    def collect_target(self,out,stage):
        archive=f'{DIRECTORY}/{LABEL}-{stage}.tar.gz'
        self.ssh(f'test ! -e {archive} && tar --exclude="{LABEL}/before/ycsb" --exclude="{LABEL}/selftest-*/db" '
            f'--exclude="{LABEL}/benefit_device_smoke" --exclude="{LABEL}/reset_generation_probe" '
            f'-czf {archive} -C {DIRECTORY} {LABEL}',180)
        self.command(self.scp()+[f'femu@127.0.0.1:{archive}',str(out)],180)
        self.command(['tar','-xzf',str(out/Path(archive).name),'-C',str(out)])

    def freeze(self,stage,files):
        out=OUTPUT/(stage+'源码');out.mkdir()
        self.command(['cp',*map(str,files),str(Path(__file__).resolve()),str(out)])
        for name,paths in (('分析',(PROJECT/'research/rrzc').glob('*.py')),('监督',TESTS.glob('*.py'))):
            d=out/name;d.mkdir();self.command(['cp',*map(str,paths),str(d)])
        (out/'FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        return out

    def build(self):
        source=self.freeze('构建',FILES)
        parent=json.loads((OLD/'同库身份.json').read_text())
        out=OUTPUT/'build';out.mkdir()
        try:
            self.start('build')
            before=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(parent))
            require(hashes(before)==parent,'父软件身份不符')
            manifest=source/'parent.software.sha256';manifest.write_text(before)
            self.ssh(f'test ! -e {TARGET} && mkdir {TARGET}')
            frozen=[source/p.name for p in FILES]
            self.command(self.scp()+[*map(str,frozen),str(manifest),f'femu@127.0.0.1:{TARGET}/'])
            self.ssh(f'nohup setsid bash {TARGET}/build_direct_guest.sh < /dev/null > {TARGET}/launcher.log 2>&1 &')
            self.record(阶段='统一模型库编译、重链接及旧新请求一致性回归')
            self.wait_for(TARGET+'/build.finished',f'test ! -f {TARGET}/library-build.log || tail -1 {TARGET}/library-build.log',1500)
        finally:self.finish_stage(out,'build')
        built=out/LABEL
        require((built/'build.exit').read_text().strip()=='0' and (built/'selftest.exit').read_text().strip()=='0','真实编译或普通目录测试失败')
        software=hashes((built/'software.sha256').read_text())
        allowed={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/zbd_zenfs.cc','plugin/zenfs/fs/zbd_zenfs.h','plugin/zenfs/fs/io_zenfs.cc',
            'plugin/zenfs/fs/rrzc_weighted_selector.h',
            'librocksdb_debug.a',DIRECTORY+'/ycsb/ycsb',DIRECTORY+'/verify_ycsb'}
        require({k for k,v in parent.items() if software.get(k)!=v}==allowed,'软件变化超出统一入口声明')
        for n in ('rrzc_direct_selector.h','rrzc_direct_zenfs.inc','rrzc_zone_content.h','rrzc_weighted_selector.h'):
            require(software['plugin/zenfs/fs/'+n]==hashlib.sha256((source/n).read_bytes()).hexdigest(),'统一模型冻结身份不符')
        (OUTPUT/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='统一模型编译通过，构建客机已关闭')

    def smoke(self):
        from direct_analysis import verify_smoke
        self.parent_image=BUILD
        out=OUTPUT/'smoke';out.mkdir();remote='/home/femu/rrzc-benefit-device-results/benefit'
        try:
            self.start('smoke')
            self.root(f'nohup setsid bash {TARGET}/run_direct_smoke_guest.sh < /dev/null > {TARGET}/smoke-launcher.log 2>&1 &')
            self.record(阶段='统一模型真实ZNS功能短测，非YCSB性能结论')
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',300)
        finally:self.finish_stage(out,'smoke',remote)
        result=verify_smoke(out/'benefit')
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append('smoke');self.record(阶段='统一排序功能与完整数据校验通过，客机已关闭')
        self.spare_smoke()

    def spare_smoke(self):
        from direct_analysis import verify_spare_smoke
        self.parent_image=BUILD
        out=OUTPUT/'spare';out.mkdir();remote='/home/femu/rrzc-benefit-device-results/benefit'
        try:
            self.start('spare')
            self.root(f'nohup setsid bash {TARGET}/run_direct_smoke_guest.sh spare < /dev/null > {TARGET}/spare-launcher.log 2>&1 &')
            self.record(阶段='真实替补回归：普通空区零、尾部8MiB、原GC迁移32MiB并Reset')
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',300)
        finally:self.finish_stage(out,'spare',remote)
        result=verify_spare_smoke(out/'benefit')
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append('spare');self.record(阶段='真实替补回归及280MiB完整数据验证通过，客机已关闭')
        self.content_smoke()

    def content_smoke(self):
        from direct_analysis import verify_content_smoke
        self.parent_image=BUILD
        out=OUTPUT/'content';out.mkdir();remote='/home/femu/rrzc-benefit-device-results/benefit'
        try:
            self.start('content')
            self.root(f'nohup setsid bash {TARGET}/run_direct_smoke_guest.sh content < /dev/null > {TARGET}/content-launcher.log 2>&1 &')
            self.record(阶段='公共内容隔离回归：同寿命元数据与SST双向隔离，两次真实RR及重挂载校验')
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',180)
        finally:self.finish_stage(out,'content',remote)
        result=verify_content_smoke(out/'benefit')
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append('content');self.record(阶段='内容隔离、两次RR及18MiB全量/重挂载分类恢复通过，客机已关闭')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('build','pair'),required=True)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();preflight(args.phase)
    if args.check:print('隔离路径、父镜像及资源核验通过，未启动');return
    password=getpass.getpass('隔离客机认证（仅内存）：')
    if args.phase=='build':OUTPUT.mkdir()
    if args.detach:
        r,w=os.pipe();pid=os.fork()
        if pid:
            os.close(w);require(os.read(r,1)==b'1','分离监督未就绪');os.close(r)
            print(f'监督PID={pid}，状态={OUTPUT / "状态.json"}',flush=True);return
        os.close(r);os.setsid();fd=os.open(OUTPUT/(args.phase+'监督.log'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0);os.write(w,b'1');os.close(w)
    run=DirectRun(SimpleNamespace(label=LABEL,seconds=1200,space_weight=50),password,OUTPUT)
    try:
        if args.phase=='build':run.build();run.smoke()
        else:
            from run_direct_pair import run_pair
            run_pair(run)
    except BaseException as error:
        run.record(阶段='本轮停止，未自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__)
        raise


if __name__=='__main__':main()
