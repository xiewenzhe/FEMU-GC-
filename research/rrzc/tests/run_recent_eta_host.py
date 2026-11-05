#!/usr/bin/env python3
"""同库原十秒ETA与近期ETA受控短配对；新增只读修改龄/寿命提示，不当作寿命预测。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import signal

import run_joint_host as base
from run_joint_host import JointRun, require, PROJECT, IMAGE_DIR, digest_manifest

LABEL='joint-recent-eta-v1-20261020'
PARENT=IMAGE_DIR/'joint-history-v3-20261020-build.qcow2'
MANIFEST=IMAGE_DIR/'joint-history-v3-20261020-results/同库身份.json'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/name for name in ('build_recent_eta_guest.sh','run_recent_eta_guest.sh',
    'joint_opportunity_smoke.cc','v1_execution_smoke_test.cc','test_recent_eta_selector.cc')]
FILES += [PROJECT/'research/rrzc/model/rrzc_recent_eta_selector.h',PROJECT/'research/rrzc/zenfs-recent-eta.patch']


class RecentRun(JointRun):
    def build(self):
        frozen=self.output/'源码';frozen.mkdir()
        for path in FILES+[Path(__file__).resolve(),Path(base.__file__).resolve()]:
            (frozen/path.name).write_bytes(path.read_bytes())
        (frozen/'身份.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in FILES},ensure_ascii=False,indent=2))
        expected=json.loads(MANIFEST.read_text());out=self.output/'build';out.mkdir()
        try:
            self.start('build')
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected))
            require(digest_manifest(raw)==expected,'真实停止父镜像的软件身份不符')
            parent=frozen/'parent.software.sha256';parent.write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            self.command(self.scp()+[str(frozen/p.name) for p in FILES]+[str(parent),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_recent_eta_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='隔离编译近期ETA与只读时间信号；原策略默认不变')
            self.wait_for(self.target+'/build.finished',f'test ! -f {self.target}/library-build.log || tail -1 {self.target}/library-build.log',1200)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','客机编译失败，保留原始证据，不自动重跑')
        software=digest_manifest((built/'software.sha256').read_text())
        require(software['plugin/zenfs/fs/rrzc_direct_selector.h']==expected['plugin/zenfs/fs/rrzc_direct_selector.h'],
                '不应修改原直接模型内核')
        require(software['plugin/zenfs/fs/rrzc_recent_eta_selector.h']==hashlib.sha256((frozen/'rrzc_recent_eta_selector.h').read_bytes()).hexdigest(),
                '客机新内核和冻结源不符')
        (self.output/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='客机真实编译通过并已关闭')

    def arm(self,scenario,mode):
        tag=scenario+'-'+mode;self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-joint-results/'+scenario
        expected=json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(tag)
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected))
            require(digest_manifest(raw)==expected,'业务前同库身份不一致')
            self.root(f'nohup setsid bash {self.target}/run_recent_eta_guest.sh {mode} {scenario} < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：固定读取及原维护，文件时间观测不参与选区',客机结果目录=remote)
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',300)
        finally:self.finish(out,remote)
        root=out/scenario
        require(all((root/name).read_text().strip()=='0' for name in ('wrapper.exit','run.exit','verify.exit')),
                '功能运行或独立全量候选校验失败，停止下一臂')
        self.state['已完成'].append(tag);self.record(阶段=tag+'完成、归档及关机；等待独立ETA及维护核验')


def interrupted(*_):raise KeyboardInterrupt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default=LABEL)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();args.phase='all';args.scenarios=['opportunity','retire']
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(args)
    for case in args.scenarios:
        for path in (IMAGE_DIR/f'{args.label}-{case}-recent.qcow2',Path(f'/tmp/{args.label}-{case}-recent.pid'),
                     Path(f'/tmp/{args.label}-{case}-recent.qmp'),Path(f'/tmp/{args.label}-{case}-recent-ssh.sock')):
            require(not path.exists(),'近期ETA目标已存在，拒绝覆盖：'+str(path))
    if args.check:print('停止父镜像、独立路径、4GiB锁页及资源通过；未启动');return
    password=getpass.getpass('本轮隔离客机认证（仅驻留内存）：')
    output.mkdir()
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='验证近期同块ETA与集中预读偏差；观测修改龄/寿命提示可用性，不声称解决未来退役',
        父镜像=str(PARENT),场景=args.scenarios,模式=['direct','recent'],
        唯一策略差异='原十秒平均ETA或同块0.5至2秒增长ETA；不先选Greedy、不加时间权重',
        不变='4GiB/128MiB区/RR1024/原GC20%/候选资格/留存预测/目标分配/后续读取及删除计划',
        时间信号='两臂只读记录修改时间和寿命提示，修改龄不是出生年龄或剩余寿命，不进入评分',
        验收='源映射/RC/同块增量及代际配对，ETA重算，候选完整载荷校验，复制与Reset账目、RR健康',
        边界='非YCSB；预测变准不等于RR/迁移下降；稳定历史无法知道突然退役，负结果照实保留',
        收尾='一台串行，每臂归档及正常关机，关闭SSH控制进程；失败不自动重跑'),ensure_ascii=False,indent=2))
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
    run=RecentRun(args,password,output,profile)
    try:
        run.build()
        for scenario in args.scenarios:
            for mode in ('direct','recent'):run.arm(scenario,mode)
        run.record(阶段='两类四臂完成，全部客机已关闭，等待独立核验')
    except BaseException as error:
        run.record(阶段='本轮停止，未自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__)
        raise
    finally:run.password=None


if __name__=='__main__':main()
