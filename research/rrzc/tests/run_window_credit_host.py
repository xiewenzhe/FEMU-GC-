#!/usr/bin/env python3
"""隔离编译窗口模型并串行做真实短对照；任何失败都先采证和正常关机。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess

import run_joint_host as base
from run_joint_host import JointRun, require, PROJECT, IMAGE_DIR, digest_manifest

LABEL='joint-window-credit-v1-20261021'
PARENT=IMAGE_DIR/'joint-rr-fair-ycsb-v1-20261021-build.qcow2'
MANIFEST=IMAGE_DIR/'joint-rr-fair-long-v3-20261021-results/同库身份.json'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/n for n in ('build_window_credit_guest.sh','run_window_credit_guest.sh',
    'joint_opportunity_smoke.cc','v1_execution_smoke_test.cc','test_window_credit_selector.cc')]
FILES += [PROJECT/'research/rrzc/model/rrzc_window_credit_selector.h',PROJECT/'research/rrzc/zenfs-window-credit.patch']


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):h.update(part)
    return h.hexdigest()


def resources(profile):
    """不触碰不明客机；默认禁止与已有本项目模拟盘叠加启动。"""
    reply=subprocess.run(['ps','-C','qemu-system-x86_64','-o','pid=,user=,rss=,args='],
        capture_output=True,text=True,check=False)
    require(reply.returncode in (0,1),'无法核验宿主客机进程')
    own=[line for line in reply.stdout.splitlines() if str(IMAGE_DIR)+'/' in line or
         str(PROJECT/'build-femu/qemu-system-x86_64') in line or '-name FEMU-joint-' in line]
    require(not own,'仍有本项目客机，先核验用途并收尾，不叠加：'+'\n'.join(own))
    memory=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    available=int(memory['MemAvailable'].split()[0])*1024
    memlock=resource.getrlimit(resource.RLIMIT_MEMLOCK)[0]
    base.check_memory(profile,available,memlock)
    stat=os.statvfs(IMAGE_DIR);free=stat.f_bavail*stat.f_frsize
    require(free>=24 << 30,'data盘余量不足24GiB')
    return dict(宿主QEMU进程=reply.stdout.splitlines(),可用内存字节=available,
        锁页软额度字节=memlock,data盘可用字节=free,预计客机和NAND约GiB=8,额外内存保护GiB=16)


class WindowRun(JointRun):
    def start(self,mode):
        self.record(启动前资源=resources(self.profile))
        require(digest(PROJECT/'build-femu/qemu-system-x86_64')==self.qemu_digest,'FEMU二进制运行期间变化')
        super().start(mode)

    def collect(self,out,remote):
        # 关机前核对远端与本地完整文件集合和哈希，不能只凭scp退出码称归档完成。
        source=digest_manifest(self.ssh(f'find {remote} -type f -exec sha256sum {{}} +',120))
        require(source,'归档远端没有可读文件：'+remote)
        super().collect(out,remote)
        local=out/Path(remote).name
        expected={}
        for name,value in source.items():
            p=Path(name);relative=p.relative_to(remote)
            require('..' not in relative.parts,'归档路径越界')
            expected[str(relative)]=value
        actual={str(p.relative_to(local)):digest(p) for p in local.rglob('*') if p.is_file()}
        require(actual==expected,'归档文件集合或哈希不符：'+remote)
        with (out/(Path(remote).name+'.归档核验.json')).open('x') as stream:
            json.dump(dict(核验通过=True,文件SHA256=actual),stream,ensure_ascii=False,indent=2)

    def build(self):
        frozen=self.output/'源码';frozen.mkdir()
        sources=FILES+[Path(__file__).resolve(),Path(base.__file__).resolve(),
            TESTS/'run_tail_ablation_fixed_pair.py',TESTS/'run_stream_pair.py']
        for p in sources:(frozen/p.name).write_bytes(p.read_bytes())
        analysis=frozen/'分析';analysis.mkdir()
        for p in (PROJECT/'research/rrzc').glob('*.py'):(analysis/p.name).write_bytes(p.read_bytes())
        (frozen/'身份.json').write_text(json.dumps({p.name:digest(p) for p in sources},ensure_ascii=False,indent=2))
        expected=json.loads(MANIFEST.read_text());out=self.output/'build';out.mkdir()
        try:
            self.start('build')
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120)
            require(digest_manifest(raw)==expected,'停止父镜像实际软件身份不符')
            manifest=frozen/'parent.software.sha256';manifest.write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            self.command(self.scp()+[str(frozen/p.name) for p in FILES]+[str(manifest),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_window_credit_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='新子镜像编译窗口评分核、ZenFS整库及受控程序，不覆盖旧客户端')
            self.wait_for(self.target+'/build.finished',f'test ! -f {self.target}/library-build.log || tail -1 {self.target}/library-build.log',1200)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','客机构建或构建核验失败，原始日志保留，不自动重跑')
        software=digest_manifest((built/'software.sha256').read_text())
        changed={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','librocksdb_debug.a'}
        require(all(software.get(n)==v for n,v in expected.items() if n not in changed),
            '旧评分核、RR队列或旧客户端发生变化')
        require(software['librocksdb_debug.a']!=expected['librocksdb_debug.a'],'未确认新库编译')
        require(software['plugin/zenfs/fs/rrzc_window_credit_selector.h']==
            digest(frozen/'rrzc_window_credit_selector.h'),'实际部署评分核与冻结源码不符')
        (self.output/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='新模型整库及受控程序编译通过，已归档正常关机')

    def arm(self,scenario,mode):
        tag=scenario+'-'+mode;self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-joint-results/'+scenario
        expected=json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(tag)
            actual=digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120))
            require(actual==expected,'受控运行前软件身份不符')
            self.root(f'nohup setsid bash {self.target}/run_window_credit_guest.sh {mode} {scenario} < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：真实读取及原生ZC/RR，非YCSB',客机结果目录=remote)
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',300)
        finally:self.finish(out,remote)
        from analyze_window_credit import analyze_arm
        result=analyze_arm(out/scenario,mode,scenario)
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append(tag)
        self.record(阶段=tag+'数据、实际评分及原RR健康核验通过，已归档关闭',
            最近结果={n:result[n] for n in ('ZC次数','RR次数','总复制字节','总成功ZoneReset')})


def interrupted(*_):raise KeyboardInterrupt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',default=LABEL)
    parser.add_argument('--scenarios',nargs='+',choices=base.CASES,default=['opportunity','stopped','low-yield','retire'])
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();args.phase='all'
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(args);before=resources(profile)
    for case in args.scenarios:
        for p in (IMAGE_DIR/f'{args.label}-{case}-window.qcow2',Path(f'/tmp/{args.label}-{case}-window.pid'),
            Path(f'/tmp/{args.label}-{case}-window.qmp'),Path(f'/tmp/{args.label}-{case}-window-ssh.sock')):
            require(not p.exists(),'新目标已存在，拒绝覆盖：'+str(p))
    if args.check:print(json.dumps(dict(只读预检通过=True,未启动客机=True,资源=before),ensure_ascii=False,indent=2));return
    password=getpass.getpass('窗口模型隔离客机认证（仅驻留内存）：')
    output.mkdir();qemu_digest=digest(PROJECT/'build-femu/qemu-system-x86_64')
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='真实部署并验证窗口自适应净维护成本，不将历史重放当作实验',
        父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=qemu_digest,
        场景=args.scenarios,模式=['greedy','window'],初始资源=before,
        唯一差异='相同新库、安全路径及RR公平队列；Greedy零抵扣，窗口模型直接按净成本/回收排序',
        不变='4GiB功能盘/32个128MiB区/128物理块每区/RR1024/GC20%/原动态资格/额外回收门槛0',
        读取='复用原受控程序的确定性读取计划，仅用于机会验证，不是YCSB限速实验；不注入RC',
        验收='新库及新程序真实编译；按精确发布序号重建留存、同块RC核验与独立整数重算；载荷及维护账目',
        风险='短历史有误差，不能预测突然删除；预期可有负收益，不以必须改善作为验收标准',
        收尾='逐臂归档及哈希核验后正常关机、PID退出、SSH关闭；失败停止不自动重跑',
        下一阶段='四类功能及账目通过后再独立重链接YCSB，不在本入口自动进入长测',
        边界='非32GiB全规模、非稳态性能实验；qcow2不保存模拟NAND/RC内存状态'),ensure_ascii=False,indent=2))
    if args.detach:
        r,w=os.pipe();pid=os.fork()
        if pid:
            os.close(w);require(os.read(r,1)==b'1','监督分离失败');os.close(r)
            print(f'监督PID={pid}，状态={output / "状态.json"}',flush=True);return
        os.close(r);os.setsid()
        fd=os.open(output/'监督.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
        os.write(w,b'1');os.close(w)
    signal.signal(signal.SIGTERM,interrupted)
    run=WindowRun(args,password,output,profile);run.qemu_digest=qemu_digest
    try:
        run.build()
        from analyze_window_credit import analyze_pair
        for scenario in args.scenarios:
            for mode in ('greedy','window'):run.arm(scenario,mode)
            result=analyze_pair(output,scenario)
            with (output/(scenario+'配对核验.json')).open('x') as stream:
                json.dump(result,stream,ensure_ascii=False,indent=2)
            run.record(阶段=scenario+'配对验收通过，已正常关机，不预设正收益',
                最近差值={n:result[n] for n in ('窗口内RR减少','窗口内复制减少字节','窗口内ZoneReset减少')})
        run.record(阶段='四类真实短对照已完成并归档，全部客机关闭，尚未运行新YCSB')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__)
        raise
    finally:run.password=None


if __name__=='__main__':main()
