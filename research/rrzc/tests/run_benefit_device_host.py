#!/usr/bin/env python3
"""收益预算客机完整构建与两次串行功能短测；每阶段均归档并正常关机。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from types import SimpleNamespace
from run_ycsb_read_pilot_host import TargetRun
from run_tail_ablation_fixed_pair import running_parent
from run_stream_pair import PROJECT,IMAGE_DIR,require
from run_rocksdb_guard_pair_host import hashes

LABEL='benefit-device-20261019'
NAME=LABEL
OUTPUT=IMAGE_DIR/(LABEL+'-results')
PARENT=IMAGE_DIR/'ycsb-locality-20261019-build.qcow2'
BUILD=IMAGE_DIR/(LABEL+'-build.qcow2')
PREVIOUS=IMAGE_DIR/'ycsb-locality-20261019-results'
DIRECTORY='/home/femu/rrzc-ycsb-retention-20261017-tests'
TARGET=DIRECTORY+'/'+NAME
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/n for n in ('build_benefit_device_guest.sh','run_benefit_device_guest.sh',
    'benefit_device_smoke.cc','v1_execution_smoke_test.cc','test_benefit_budget.cc','workload-locality-wide-zns.properties')]
FILES += [PROJECT/'research/rrzc/zenfs-benefit-budget.patch']
FILES += [PROJECT/'research/rrzc/model'/n for n in ('rrzc_net_cost_selector.h','rrzc_benefit_budget.h')]


def preflight(phase,resume=False):
    require(Path('/dev/kvm').exists(),'KVM不可用')
    parent=PARENT if phase=='build' else BUILD
    require(parent.is_file() and parent.resolve().parent==IMAGE_DIR and not running_parent(parent),'父镜像不符或运行中')
    require(not OUTPUT.exists() if phase=='build' else (OUTPUT/'同库身份.json').is_file(),'阶段结果状态不符')
    with socket.socket() as s:require(s.connect_ex(('127.0.0.1',8096))!=0,'8096被占用')
    require(not resume or phase=='smoke','恢复仅用于已归档的功能短测')
    if resume:
        archive=OUTPUT/'default/阶段归档.json'
        require(archive.is_file(),'默认组没有归档记录')
        result=json.loads(archive.read_text())
        if not result['完整']:
            # 正常关机可以先关闭SSH、稍后才退出QEMU；不覆盖原始异常证据。
            state=json.loads((OUTPUT/'状态.json').read_text());pid=state.get('当前客机PID')
            require(result['错误'] and all(e.startswith('正常关机：') for e in result['错误']) and
                pid and state['当前镜像']==str(IMAGE_DIR/f'{LABEL}-default.qcow2') and
                not Path(f'/proc/{pid}').exists() and not running_parent(IMAGE_DIR/f'{LABEL}-default.qcow2'),
                '默认组未完成归档或客机尚未退出，不能跳过')
    for mode in (('build',) if phase=='build' else ('benefit',) if resume else ('default','benefit')):
        for path in (IMAGE_DIR/f'{LABEL}-{mode}.qcow2',Path(f'/tmp/{LABEL}-{mode}.pid'),
            Path(f'/tmp/{LABEL}-{mode}.qmp'),Path(f'/tmp/{LABEL}-{mode}-ssh.sock')):
            require(not path.exists(),'目标已存在：'+str(path))
    stat=os.statvfs(IMAGE_DIR);require(stat.f_bavail*stat.f_frsize>=24<<30,'data盘不足24GiB')
    mem=dict(s.split(':',1) for s in Path('/proc/meminfo').read_text().splitlines())
    require(int(mem['MemAvailable'].split()[0])>=16*1024*1024,'可用内存不足16GiB')


class DeviceRun(TargetRun):
    label_prefix=LABEL
    parent_image=PARENT
    port='8096'

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        # 此处是编译与文件功能测试，不继承YCSB旧监督器的60万请求标签。
        self.state.pop('业务请求数',None);self.state.pop('观察上限秒数',None)
        if (OUTPUT/'同库身份.json').is_file():self.state['已完成']=['build']

    def shutdown(self):
        pid=self.state.get('当前客机PID')
        try:return super().shutdown()
        except Exception as error:
            # 断开SSH不等于关机失败；仅按已验证的原PID是否真正退出判定。
            kind=type(error).__name__
        for _ in range(60):
            if not Path(f'/proc/{pid}').exists():
                self.record(关机反馈=kind+'；原客机PID随后已退出，未强制终止');return
            time.sleep(1)
        raise RuntimeError('正常关机后原PID仍存活，停止后续实验；连接反馈类型='+kind)

    def collect_target(self,out,stage):
        archive=f'{DIRECTORY}/{NAME}-{stage}.tar.gz'
        self.ssh(f'test ! -e {archive} && tar --exclude="{NAME}/before/ycsb" '
            f'--exclude="{NAME}/selftest-*/db" --exclude="{NAME}/benefit_device_smoke" '
            f'-czf {archive} -C {DIRECTORY} {NAME}',180)
        self.command(self.scp()+[f'femu@127.0.0.1:{archive}',str(out)],180)
        self.command(['tar','-xzf',str(out/Path(archive).name),'-C',str(out)])

    def finish_stage(self,out,stage,remote=None):
        errors=[]
        try:self.collect_target(out,stage)
        except Exception as error:errors.append('构建日志归档：'+str(error))
        if remote:
            try:self.collect(out,remote)
            except Exception as error:errors.append('设备日志归档：'+str(error))
        try:
            if self.state.get('当前客机PID') and Path(f"/proc/{self.state['当前客机PID']}").exists():self.shutdown()
            self.record(当前客机PID=None,阶段=stage+'已结束并正常关机',归档错误=errors)
        except Exception as error:
            errors.append('正常关机：'+str(error));self.record(阶段='关机未完成，需核验',错误=str(error))
        (out/'阶段归档.json').write_text(json.dumps(dict(完整=not errors,错误=errors),ensure_ascii=False,indent=2))
        require(not errors,'阶段收尾存在错误，见阶段归档.json')

    def freeze(self,stage):
        target=OUTPUT/(stage+'源码');target.mkdir()
        self.command(['cp',*map(str,FILES),str(Path(__file__).resolve()),str(target)])
        analysis=target/'分析';analysis.mkdir()
        self.command(['cp',*map(str,(PROJECT/'research/rrzc').glob('*.py')),str(analysis)])
        (target/'FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        return target

    def build(self):
        plan=dict(目的='编译独立收益预算入口，先普通目录与原二进制回归，再串行设备功能测试',
            父镜像=str(PARENT),模式顺序=['build','default','benefit'],
            不变项='FEMU、8GiB几何、RR4096、GC20%、192MiB空间资格、10秒界限、旧策略及公共分配/迁移机制',
            修改='仅新默认关闭模型头和一个fs_zenfs.cc补丁；重编库并重链接YCSB/验证器/设备测试程序',
            功能载荷='24个256MiB填充区、冷16MiB与热40MiB候选，3904次预读、随后32读每秒至首次维护且最多15秒；不是YCSB性能负载',
            预期='默认仍Greedy；新入口仅净收益可补偿24MiB额外搬运且实时复核通过时改选',
            验收='独立逐轮重算建议/执行预算，复用原源及尾部复核；全量比对56MiB有效载荷，填充首尾抽检；不要求性能正收益',
            清理='每阶段归档并正常关机，失败不自动重跑、不保留闲置客机、不启动长测')
        (OUTPUT/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        source=self.freeze('构建')
        parent=json.loads((PREVIOUS/'同库身份.json').read_text())
        output=OUTPUT/'build';output.mkdir()
        try:
            self.start('build')
            before=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(parent))
            require(hashes(before)==parent,'父镜像身份不同')
            manifest=source/'parent.software.sha256';manifest.write_text(before)
            self.ssh(f'test ! -e {TARGET} && mkdir {TARGET}')
            self.command(self.scp()+[*map(str,FILES),str(manifest),f'femu@127.0.0.1:{TARGET}/'])
            self.ssh(f'nohup setsid bash {TARGET}/build_benefit_device_guest.sh < /dev/null > {TARGET}/launcher.log 2>&1 &')
            self.record(阶段='完整编译收益预算接线与库、重链接及普通目录回归')
            self.wait_for(f'{TARGET}/build.finished',f'test ! -f {TARGET}/library-build.log || tail -1 {TARGET}/library-build.log',1500)
        finally:self.finish_stage(output,'build')
        built=output/NAME
        require((built/'build.exit').read_text().strip()=='0' and (built/'selftest.exit').read_text().strip()=='0','编译或普通目录校验失败')
        software=hashes((built/'software.sha256').read_text())
        allowed={'plugin/zenfs/fs/fs_zenfs.cc','librocksdb_debug.a',DIRECTORY+'/ycsb/ycsb',DIRECTORY+'/verify_ycsb'}
        require({n for n,d in parent.items() if software.get(n)!=d}==allowed,'软件变化超出声明范围')
        for name in ('rrzc_benefit_budget.h','rrzc_net_cost_selector.h'):
            require(software['plugin/zenfs/fs/'+name]==hashlib.sha256((source/name).read_bytes()).hexdigest(),'新模型头身份不同')
        with tempfile.TemporaryDirectory(prefix='rrzc-benefit-build-verify-') as tmp:
            root=Path(tmp);(root/'fs').mkdir();shutil.copyfile(built/'before/fs_zenfs.cc',root/'fs/fs_zenfs.cc')
            subprocess.run(['patch','--fuzz=0','-p1','-i',str(source/'zenfs-benefit-budget.patch')],cwd=root,check=True,capture_output=True)
            require((root/'fs/fs_zenfs.cc').read_bytes()==(built/'fs_zenfs.compiled.cc').read_bytes(),'客机接线与冻结补丁不同')
        (OUTPUT/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.record(阶段='完整编译及普通目录回归通过，构建客机已关闭',当前客机PID=None,已完成=['build'])

    def smoke(self,resume=False):
        from benefit_log import verify_smoke
        self.freeze('复核恢复' if resume else '短测')
        self.parent_image=BUILD
        expected=json.loads((OUTPUT/'同库身份.json').read_text())
        if resume:
            result=verify_smoke(OUTPUT/'default/default','default')
            target=OUTPUT/'default/恢复独立核验.json'
            require(not target.exists(),'恢复核验已存在，拒绝覆盖')
            target.write_text(json.dumps(result,ensure_ascii=False,indent=2))
            (OUTPUT/'default/收尾复核.json').write_text(json.dumps(dict(
                说明='原关机SSH先断开，独立核验原PID随后已退出；不修改原异常归档，不重跑默认组',
                默认镜像不再运行=not running_parent(IMAGE_DIR/f'{LABEL}-default.qcow2'),
                数据与逐轮核验=True),ensure_ascii=False,indent=2))
            self.state['已完成']=['build','default']
            self.record(阶段='只重算已归档默认组，数据核验通过；不重跑设备实验')
        for mode in (('benefit',) if resume else ('default','benefit')):
            out=OUTPUT/mode;out.mkdir();remote='/home/femu/rrzc-benefit-device-results/'+mode
            try:
                self.start(mode)
                before=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected))
                require(hashes(before)==expected,'短测软件与构建不符')
                self.root(f'nohup setsid bash {TARGET}/run_benefit_device_guest.sh {mode} < /dev/null > {TARGET}/device-launcher.log 2>&1 &')
                self.record(阶段=mode+'设备功能测试')
                self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',300)
            finally:self.finish_stage(out,mode,remote)
            result=verify_smoke(out/mode,mode)
            (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            self.state.setdefault('已完成',[]).append(mode)
            self.record(阶段=mode+'功能与数据核验通过，客机已关闭')
        self.record(阶段='两次设备功能联调通过，客机全部关闭；尚无YCSB性能对照',当前客机PID=None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('build','smoke'),required=True)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    parser.add_argument('--resume',action='store_true',help='仅重算已关闭默认组归档，再运行尚未启动的新入口组')
    args=parser.parse_args();preflight(args.phase,args.resume)
    if args.check:print('父镜像、端口、目标与资源核验通过，未启动');return
    password=getpass.getpass('隔离客机认证（仅内存）：')
    if args.phase=='build':OUTPUT.mkdir()
    if args.detach:
        ready_read,ready_write=os.pipe()
        pid=os.fork()
        if pid:
            os.close(ready_write)
            require(os.read(ready_read,1)==b'1','分离监督未就绪')
            os.close(ready_read)
            print(f'监督PID={pid}，状态={OUTPUT / "状态.json"}',flush=True);return
        os.close(ready_read)
        os.setsid();log=os.open(OUTPUT/(args.phase+'监督.log'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(log,1);os.dup2(log,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
        # 客机启动前先完成新会话及日志接管，避免父进程退出造成PTY挂断竞态。
        os.write(ready_write,b'1');os.close(ready_write)
    run=DeviceRun(SimpleNamespace(label=LABEL,seconds=0,space_weight=50),password,OUTPUT)
    run.state['模式顺序']=['build','default','benefit']
    try:
        if args.phase=='build':run.build()
        else:run.smoke(args.resume)
    except BaseException as error:
        run.record(阶段='本轮停止，不自动重跑',错误=str(error) or type(error).__name__);raise


if __name__=='__main__':main()
