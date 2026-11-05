#!/usr/bin/env python3
"""独立编译RR反饥饿修复，再跑不限速小测/40万请求配对；不改原30秒健康检查。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import signal

import run_joint_host as base
import run_stable_ycsb_host as ycsb
from run_joint_host import require,PROJECT,IMAGE_DIR,digest_manifest

LABEL='joint-rr-fair-ycsb-v1-20261021'
PARENT=ycsb.PARENT
MANIFEST=ycsb.MANIFEST
TESTS=PROJECT/'research/rrzc/tests'
FIX_FILES=[TESTS/'build_rr_fair_guest.sh',TESTS/'test_rr_fair_queue.cc',
           PROJECT/'research/rrzc/model/rrzc_rr_fair_queue.h',PROJECT/'research/rrzc/zenfs-rr-fair-queue.patch']


class FairRun(ycsb.StableYcsbRun):
    def __init__(self,*args):
        super().__init__(*args)
        self.state['边界']='4GiB真实不限速YCSB，验证共享RR排队修复；非32GiB全规模或已证明稳态'

    def root(self,command,timeout=45):
        if self.target+'/run_stable_ycsb_guest.sh ' in command:
            require(command.count('nohup setsid bash ')==1,'未知业务启动结构')
            command=command.replace('nohup setsid bash ','nohup setsid env ZENFS_RR_FAIR_QUEUE=1 bash ',1)
        return super().root(command,timeout)

    def build(self):
        frozen=self.output/'RR修复源码';frozen.mkdir()
        for p in FIX_FILES+[Path(__file__).resolve()]:(frozen/p.name).write_bytes(p.read_bytes())
        (frozen/'身份.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in FIX_FILES},ensure_ascii=False,indent=2))
        expected=json.loads(MANIFEST.read_text());out=self.output/'rr-build';out.mkdir()
        original_target=self.target;self.target=original_target+'-rr-fix'
        try:
            try:
                self.start('rr-build')
                raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected))
                require(digest_manifest(raw)==expected,'停止父镜像身份不符')
                manifest=frozen/'parent.software.sha256';manifest.write_text(raw)
                self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
                self.command(self.scp()+[str(frozen/p.name) for p in FIX_FILES]+[str(manifest),f'femu@127.0.0.1:{self.target}/'])
                self.ssh(f'nohup setsid bash {self.target}/build_rr_fair_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
                self.record(阶段='只修复RR地址饥饿：编译每实例等待队列，不改阈值/周期/一轮一区')
                self.wait_for(self.target+'/build.finished',f'test ! -f {self.target}/library-build.log || tail -1 {self.target}/library-build.log',1200)
            finally:self.finish(out)
            built=out/Path(self.target).name
        finally:self.target=original_target
        require((built/'build.exit').read_text().strip()=='0','RR修复构建失败，原始错误保留，不自动重跑')
        software=digest_manifest((built/'software.sha256').read_text())
        for name in ('rrzc_direct_selector.h','rrzc_recent_eta_selector.h','rrzc_stable_credit_selector.h','rrzc_direct_zenfs.inc','snapshot.h'):
            require(software['plugin/zenfs/fs/'+name]==expected['plugin/zenfs/fs/'+name],'不应改变ZC模型或原评分接线：'+name)
        require(software['plugin/zenfs/fs/rrzc_rr_fair_queue.h']==hashlib.sha256((frozen/'rrzc_rr_fair_queue.h').read_bytes()).hexdigest(),'队列部署源不符')
        require(software['librocksdb_debug.a']!=expected['librocksdb_debug.a'],'未确认修复库重编译')
        identity=self.output/'RR修复库身份.json';identity.write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.state['已完成'].append('rr-build');self.record(阶段='RR修复库编译通过、已归档关机，继续独立重链接YCSB')
        self.parent_image=IMAGE_DIR/f'{self.label_prefix}-rr-build.qcow2'
        previous=ycsb.MANIFEST;ycsb.MANIFEST=identity
        try:super().build()
        finally:ycsb.MANIFEST=previous

    def arm(self,stage,mode):
        super().arm(stage,mode)
        tag=stage+'-'+mode;root=self.output/tag/tag
        require((root/'RR调度.txt').read_text().strip()=='等待优先开关=1','未将RR修复一致启用')
        zen=(root/'zenfs.run.log').read_text()
        require('RR公平队列配置：启用=1，每轮最多维护=1，原阈值=1024，未改变检查周期=1' in zen,'未确认实际RR公平分支')
        require(zen.count('RR公平队列服务：')==zen.count('RR 开始：'),'RR服务与实际维护未一一对应')
        self.record(阶段=tag+'原健康门槛及公平队列实际分支均通过，客机已关闭')


def interrupted(*_):raise KeyboardInterrupt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',default=LABEL)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();args.phase='build';args.scenarios=[]
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(args)
    for tag in ('rr-build','smoke-greedy','smoke-stable','short-greedy','short-stable'):
        for p in (IMAGE_DIR/f'{args.label}-{tag}.qcow2',Path(f'/tmp/{args.label}-{tag}.pid'),
                  Path(f'/tmp/{args.label}-{tag}.qmp'),Path(f'/tmp/{args.label}-{tag}-ssh.sock')):
            require(not p.exists(),'新目标已存在，拒绝覆盖：'+str(p))
    if args.check:print('修复子镜像、停止父镜像、资源/锁页和串行路径通过；未启动');return
    password=getpass.getpass('RR修复隔离客机认证（仅驻留内存）：')
    output.mkdir();(output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='修复并验证RR低地址重复服务造成的同代际高地址饥饿，不绕过健康检查',
        原失败='/home/data/xwz/rrzc-baselines/joint-stable-ycsb-v1-20261021-results',
        变更='两臂统一启用等待优先RR，仍每约10秒最多维护一区，迁移主体/阈值/检查周期/ZC模型不变',
        策略=['同安全路径Greedy','稳定收益模型'],请求='先两臂8192键/8192请求，再491520键/400000请求，完全不限速、同种子同请求同库',
        不变='4GiB/128MiB区/RR1024/GC20%/原候选和安全资格/无WAL无块缓存/原30秒健康门槛',
        验收='真实修复库编译及YCSB重链接，原有数据/同块/评分/复制/Reset验收不放松，旧失败必须仍拒绝',
        边界='本轮只验证修复和短对照，不自动进入400万请求长测；FIFO不承诺在任意积压或长期锁占用下都能满足30秒',
        收尾='串行，逐臂归档正常关机及SSH退出；失败停止、不自动重跑、不覆盖原失败'),ensure_ascii=False,indent=2))
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
    run=FairRun(args,password,output,profile)
    try:
        run.build()
        from analyze_stable_ycsb import compare_pair
        for stage in ('smoke','short'):
            for mode in ('greedy','stable'):run.arm(stage,mode)
            result=compare_pair(output,stage)
            with (output/(stage+'配对核验.json')).open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
            run.record(阶段=stage+'配对及原RR健康检查通过，客机已归档关闭',最近差值=result['模型相对Greedy差值'])
        run.record(阶段='RR反饥饿修复的小测和40万请求配对全部通过，全部客机已关闭，未启动长测')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__);raise
    finally:run.password=None


if __name__=='__main__':main()
