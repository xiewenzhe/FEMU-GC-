#!/usr/bin/env python3
"""真实ZenFS联合维护短对照；独立缩小盘、逐臂重建NAND、串行归档及关机。"""
import argparse
import copy
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

from run_tail_ablation_fixed_pair import FixedPair, DEVICE as OLD_DEVICE, running_parent
from run_stream_pair import PROJECT, IMAGE_DIR, require

sys.path.insert(0, str(PROJECT/'research/rrzc'))
from finerr_profile import load, settings
from manual_zns import check_memory

PARENT=IMAGE_DIR/'content-guard-20261020-build.qcow2'
PARENT_MANIFEST=IMAGE_DIR/'content-guard-20261020-results/同库身份.json'
CASES=('opportunity','low-yield','stopped','retire')
FILES=[PROJECT/'research/rrzc/tests'/n for n in (
    'build_joint_guest.sh','run_joint_guest.sh','joint_opportunity_smoke.cc','v1_execution_smoke_test.cc')]
FILES += [PROJECT/'research/rrzc/zenfs-zero-reclaim.patch',
          PROJECT/'research/rrzc/model/rrzc_weighted_selector.h']


def controlled_profile():
    # 只缩小Zone数量；不改写32GiB目标参数源和每Zone物理组织。
    p=copy.deepcopy(load())
    p['名称']='联合维护受控功能盘：FineRR每Zone组织，缩小至32区；不是论文全规模实验'
    p['硬件']['每PlaneBlock数']=32
    p['命名空间']['Zone数量']=32
    p['命名空间']['容量字节']=4 << 30
    return p


def digest_manifest(text):
    return {name:digest for digest,name in (line.split(maxsplit=1) for line in text.splitlines())}


def preflight(args):
    require(re.fullmatch(r'joint-[a-z0-9-]+',args.label) is not None,'标签须为joint-开头的独立ASCII名称')
    require(len(args.scenarios)==len(set(args.scenarios)),'场景重复，拒绝覆盖同一轮次')
    require(os.access('/dev/kvm',os.R_OK|os.W_OK),'当前进程无KVM权限')
    require(PARENT.is_file() and PARENT_MANIFEST.is_file() and not running_parent(PARENT),'父镜像或身份清单不可用')
    profile=controlled_profile()
    mem=dict(row.split(':',1) for row in Path('/proc/meminfo').read_text().splitlines())
    # 沿用NAND+客机+16GiB余量，不绕过锁页保护。
    check_memory(profile,int(mem['MemAvailable'].split()[0])*1024,resource.getrlimit(resource.RLIMIT_MEMLOCK)[0])
    stat=os.statvfs(IMAGE_DIR);require(stat.f_bavail*stat.f_frsize>=24 << 30,'data盘余量不足24GiB')
    with socket.socket() as s:require(s.connect_ex(('127.0.0.1',8096))!=0,'8096已占用，不操作已有客机')
    output=IMAGE_DIR/(args.label+'-results')
    if args.phase in ('build','all'):require(not output.exists(),'结果目录已存在，拒绝覆盖或自动重跑')
    else:
        require((output/'同库身份.json').is_file(),'尚无本轮成功构建及软件身份')
        state=json.loads((output/'状态.json').read_text())
        require(state.get('当前客机PID') is None and (IMAGE_DIR/f'{args.label}-build.qcow2').is_file(),
                '上一阶段客机尚未退出或构建镜像缺失')
    modes=['build'] if args.phase=='build' else [f'{case}-{mode}' for case in args.scenarios for mode in ('greedy','direct')]
    if args.phase=='all':modes.insert(0,'build')
    for mode in modes:
        for path in (IMAGE_DIR/f'{args.label}-{mode}.qcow2',Path(f'/tmp/{args.label}-{mode}.pid'),
                     Path(f'/tmp/{args.label}-{mode}.qmp'),Path(f'/tmp/{args.label}-{mode}-ssh.sock')):
            require(not path.exists(),'目标已存在，拒绝覆盖：'+str(path))
    return output,profile


class JointRun(FixedPair):
    port='8096'

    def __init__(self,args,password,output,profile):
        super().__init__(SimpleNamespace(label=args.label,seconds=25,space_weight=50),password,output)
        self.label_prefix=args.label;self.parent_image=PARENT;self.profile=profile
        self.target='/home/femu/'+args.label
        self.state=dict(监督进程PID=os.getpid(),标签=args.label,阶段='受控短测准备',已完成=[],
                        当前客机PID=None,当前镜像=None,SSH端口=int(self.port),结果目录=str(output),
                        关机方式='记录的唯一QMP发送system_powerdown；必要时客机内正常poweroff',
                        边界='4GiB功能盘，不是32GiB论文全规模、YCSB或性能实验；不保存内存快照')
        if (output/'状态.json').is_file():
            self.state['已完成']=json.loads((output/'状态.json').read_text()).get('已完成',[])

    def start(self,mode):
        super().start(mode)
        self.record(QMP=str(self.qmp),当前阶段=mode)

    def command(self,argv,timeout=60):
        # 仅本入口替换继承启动器的设备参数，绝不修改旧入口或其冻结配置。
        if argv and str(argv[0]).endswith('qemu-system-x86_64'):
            require(argv.count(OLD_DEVICE)==1,'继承启动器参数结构变化，拒绝猜测')
            argv=[settings(self.profile)['设备参数'] if item==OLD_DEVICE else item for item in argv]
        return super().command(argv,timeout)

    def scp(self):
        return ['scp','-q','-o',f'ControlPath={self.socket}','-o','BatchMode=yes','-P',self.port]

    def wait_for(self,marker,progress,limit):
        started=time.monotonic()
        while time.monotonic()-started<limit:
            result=self.ssh(f'if test -f {marker}; then printf "阶段完成\\n"; fi; {progress}; true')
            self.record(最近观测=result)
            if '阶段完成' in result:return
            time.sleep(10)
        raise RuntimeError('阶段超时；先归档并正常关机，不自动重跑')

    def collect(self,out,remote):
        self.command(self.scp()+['-r',f'femu@127.0.0.1:{remote}',str(out)],180)

    def finish(self,out,remote=None):
        errors=[]
        for path in (self.target,remote):
            if path:
                try:self.collect(out,path)
                except Exception as error:errors.append('归档失败：'+str(error))
        pid=self.state.get('当前客机PID')
        if pid and Path(f'/proc/{pid}').exists():
            try:
                try:self.shutdown()
                except RuntimeError:
                    # 只使用已核验的该客机SSH控制连接，不按进程名强杀。
                    try:self.root('systemctl poweroff',15)
                    except RuntimeError as error:self.record(正常关机连接反馈=str(error))
                    for _ in range(60):
                        if not Path(f'/proc/{pid}').exists():break
                        time.sleep(1)
                    require(not Path(f'/proc/{pid}').exists(),'客机仍存活，不能启动下一臂')
            except Exception as error:errors.append('关机失败：'+str(error))
        alive=bool(pid and Path(f'/proc/{pid}').exists())
        if not alive:
            self.record(当前客机PID=None,当前镜像=None,阶段='本阶段归档及正常关机完成' if not errors else '归档异常，客机已退出')
            if self.socket:
                # ControlMaster不应在客机关闭后再驻留一小时。
                reply=subprocess.run(['ssh','-S',self.socket,'-O','exit','-p',self.port,
                                      'femu@127.0.0.1'],capture_output=True,text=True,timeout=15)
                (out/'SSH收尾.log').write_text(reply.stdout+reply.stderr)
        (out/'阶段归档.json').write_text(json.dumps(dict(完整=not errors,错误=errors,客机仍存活=alive),ensure_ascii=False,indent=2))
        require(not errors,'阶段归档或关机未通过，停止后续阶段')

    def build(self):
        frozen=self.output/'源码';frozen.mkdir()
        for path in FILES+[Path(__file__).resolve()]:
            (frozen/path.name).write_bytes(path.read_bytes())
        (frozen/'身份.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in FILES},ensure_ascii=False,indent=2))
        parent=json.loads(PARENT_MANIFEST.read_text());out=self.output/'build';out.mkdir()
        try:
            self.start('build')
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(parent))
            require(digest_manifest(raw)==parent,'父镜像实际软件身份不符')
            manifest=frozen/'parent.software.sha256';manifest.write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            self.command(self.scp()+[str(frozen/p.name) for p in FILES]+[str(manifest),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_joint_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='隔离客机编译零门槛支持与真实联合维护短测；模型公式不改')
            self.wait_for(self.target+'/build.finished',f'tail -1 {self.target}/library-build.log',1200)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','构建失败，原始编译日志已保留')
        software=digest_manifest((built/'software.sha256').read_text())
        for name in ('rrzc_direct_selector.h','rrzc_direct_zenfs.inc'):
            require(software['plugin/zenfs/fs/'+name]==parent['plugin/zenfs/fs/'+name],'不应修改直接模型公式或接线')
        (self.output/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='构建成功，客机已退出')

    def arm(self,scenario,mode):
        tag=scenario+'-'+mode;self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-joint-results/'+scenario
        software=json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(tag)
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(software))
            require(digest_manifest(raw)==software,'试验前软件身份不符')
            self.root(f'nohup setsid bash {self.target}/run_joint_guest.sh {mode} {scenario} < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：真实文件准备、固定读取、原ZC/RR及独立只读校验',客机结果目录=remote)
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',300)
        finally:self.finish(out,remote)
        results=out/scenario
        require(all((results/name).read_text().strip()=='0' for name in ('wrapper.exit','run.exit','verify.exit')),
                '受控运行或完整候选数据校验失败，保留结果，不自动重跑')
        self.state['已完成'].append(tag);self.record(阶段=tag+'完成并关机，维护收益尚待独立分析')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='joint-opportunity-20261020')
    parser.add_argument('--phase',choices=('build','pair','all'),required=True)
    parser.add_argument('--scenarios',nargs='+',choices=CASES,default=list(CASES))
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();output,profile=preflight(args)
    if args.check:print('独立4GiB受控盘、锁页及资源核验通过；没有启动或修改镜像');return
    password=getpass.getpass('隔离客机认证（仅驻留内存）：')
    if args.phase in ('build','all'):
        output.mkdir()
        (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
        (output/'预注册计划.json').write_text(json.dumps(dict(
            目的='固定有效量、真实同块RC及后续读取，验证联合维护机会；不要求正收益',
            场景=args.scenarios,策略=['同候选单区Greedy','不改公式的直接净成本模型'],
            准备='每臂新QEMU、新NAND；先20个填充区和候选，24%空闲关闭句柄形成真实满区留存历史33秒；固定热块928读后追加2个填充区自然降至17%空闲；候选4MiB与5/8/24MiB',
            后续='16次每秒、128次；退役场景第4秒删除且只读64次；热读停止场景不再读',
            边界='原GC20%、原候选资格、RR1024不改；取消额外固定回收门槛；非YCSB非性能测量',
            验收='固定25秒覆盖后续原生RR检查；比较源映射/RC/同块近期增量、维护完成及Reset；不宣称永久避免RR',
            清理='各阶段采证归档、QMP正常关机、PID退出核验、关闭SSH控制进程；失败停止'),ensure_ascii=False,indent=2))
    if args.detach:
        r,w=os.pipe();pid=os.fork()
        if pid:
            os.close(w);require(os.read(r,1)==b'1','监督分离失败');os.close(r)
            print(f'监督PID={pid}，状态={output / "状态.json"}',flush=True);return
        os.close(r);os.setsid()
        fd=os.open(output/(args.phase+'监督.log'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
        os.write(w,b'1');os.close(w)
    run=JointRun(args,password,output,profile)
    try:
        if args.phase in ('build','all'):run.build()
        if args.phase in ('pair','all'):
            for scenario in args.scenarios:
                for mode in ('greedy','direct'):run.arm(scenario,mode)
        run.record(阶段='所请求阶段完成；全部实验客机已退出，等待独立结果分析')
    except BaseException as error:
        run.record(阶段='受控短测停止，不自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__)
        raise
    finally:run.password=None


if __name__=='__main__':main()
