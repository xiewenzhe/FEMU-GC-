#!/usr/bin/env python3
"""独立编译稳定收益模型；同库Greedy配对，串行归档正常关机，不自动重跑。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import signal

import run_joint_host as base
from run_joint_host import JointRun, require, PROJECT, IMAGE_DIR, digest_manifest

LABEL='joint-stable-credit-v1-20261021'
PARENT=IMAGE_DIR/'joint-recent-eta-v1-20261020-build.qcow2'
MANIFEST=IMAGE_DIR/'joint-recent-eta-v1-20261020-results/同库身份.json'
TESTS=PROJECT/'research/rrzc/tests'
FILES=[TESTS/name for name in ('build_stable_credit_guest.sh','run_stable_credit_guest.sh',
    'joint_opportunity_smoke.cc','v1_execution_smoke_test.cc','test_stable_credit_selector.cc')]
FILES += [PROJECT/'research/rrzc/model/rrzc_stable_credit_selector.h',
          PROJECT/'research/rrzc/zenfs-stable-credit.patch',PROJECT/'research/rrzc/zenfs-stable-greedy-pair.patch']


class StableRun(JointRun):
    def build(self):
        frozen=self.output/'源码';frozen.mkdir()
        for path in FILES+[Path(__file__).resolve(),Path(base.__file__).resolve(),
                          TESTS/'run_tail_ablation_fixed_pair.py',TESTS/'run_stream_pair.py']:
            (frozen/path.name).write_bytes(path.read_bytes())
        analysis=frozen/'分析';analysis.mkdir()
        for p in (PROJECT/'research/rrzc').glob('*.py'):(analysis/p.name).write_bytes(p.read_bytes())
        (frozen/'身份.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in FILES},ensure_ascii=False,indent=2))
        expected=json.loads(MANIFEST.read_text());out=self.output/'build';out.mkdir()
        try:
            self.start('build')
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected))
            require(digest_manifest(raw)==expected,'停止父镜像软件身份不符')
            manifest=frozen/'parent.software.sha256';manifest.write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            self.command(self.scp()+[str(frozen/p.name) for p in FILES]+[str(manifest),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_stable_credit_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='隔离编译新模型及同安全路径Greedy；不修改旧模型与旧客户端')
            self.wait_for(self.target+'/build.finished',f'test ! -f {self.target}/library-build.log || tail -1 {self.target}/library-build.log',1200)
            unchanged={n:d for n,d in expected.items() if n not in (
                'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','librocksdb_debug.a')}
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(unchanged))
            require(digest_manifest(raw)==unchanged,'原直接/近期模型或旧程序被修改')
            (out/'旧模型不变.sha256').write_text(raw)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','客机编译失败，保留原错误，不自动重跑')
        software=digest_manifest((built/'software.sha256').read_text())
        require(software['plugin/zenfs/fs/rrzc_stable_credit_selector.h']==
                hashlib.sha256((frozen/'rrzc_stable_credit_selector.h').read_bytes()).hexdigest(),'部署新核与冻结源码不同')
        require(software['librocksdb_debug.a']!=expected['librocksdb_debug.a'],'未确认重新编译库')
        (self.output/'同库身份.json').write_text(json.dumps(software,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='新库和受控程序真实编译通过，构建客机已关闭')

    def arm(self,scenario,mode):
        tag=scenario+'-'+mode;self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-joint-results/'+scenario
        expected=json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(tag)
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected))
            require(digest_manifest(raw)==expected,'运行前同库身份不一致')
            self.root(f'nohup setsid bash {self.target}/run_stable_credit_guest.sh {mode} {scenario} < /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：真实同块读取及原生维护，非YCSB',客机结果目录=remote)
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',300)
        finally:self.finish(out,remote)
        root=out/scenario
        require(all((root/n).read_text().strip()=='0' for n in ('wrapper.exit','run.exit','verify.exit')),
                '功能运行或载荷校验失败，停止下一臂')
        from analyze_stable_credit import analyze_arm
        result=analyze_arm(root,mode,scenario)
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append(tag)
        self.record(阶段=tag+'评分、正确性及账目通过，已归档关闭',最近结果={n:result[n]
            for n in ('ZC次数','RR次数','总复制字节','总成功ZoneReset')})


def interrupted(*_):raise KeyboardInterrupt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default=LABEL)
    parser.add_argument('--scenarios',nargs='+',choices=base.CASES,default=list(base.CASES))
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();args.phase='all'
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(args)
    for case in args.scenarios:
        for path in (IMAGE_DIR/f'{args.label}-{case}-stable.qcow2',Path(f'/tmp/{args.label}-{case}-stable.pid'),
                     Path(f'/tmp/{args.label}-{case}-stable.qmp'),Path(f'/tmp/{args.label}-{case}-stable-ssh.sock')):
            require(not path.exists(),'新模型目标已存在，拒绝覆盖：'+str(path))
    if args.check:print('停止父镜像、串行资源、锁页及独立路径通过；未启动');return
    password=getpass.getpass('新隔离客机认证（仅驻留内存）：')
    output.mkdir()
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='真实编译并验证稳定速率/时间折减评分的联合维护收益与反例，不预设改善',
        父镜像=str(PARENT),场景=args.scenarios,模式=['greedy','stable'],
        唯一差异='共用Direct安全执行入口；Greedy零抵扣按有效/回收排序，稳定模型按新净成本排序',
        不变='4GiB/32个128MiB区/每区128块/RR1024/原GC20%与动态资格/额外门槛0/目标分配与安全复核',
        初态='每臂独立新QEMU/NAND，4MiB冷与5/8/24MiB热候选，真实同块928读、17%空闲；初始映射/RC/代际配对',
        请求='复用原受控程序：16读/秒，机会128读、退役64读后4秒删除、停止0读；固定25秒观察',
        验收='客机C++内核/整库/真实重链接；每臂同库、载荷、原生维护账目；逐行重算评分及同块证据',
        边界='非YCSB、非32GiB正式规模、非稳态；不证明永久避免RR；qcow2不是NAND快照',
        收尾='一台串行，每臂归档、正常关机和PID核验，关闭SSH控制连接；失败停止，不自动重跑'),
        ensure_ascii=False,indent=2))
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
    run=StableRun(args,password,output,profile)
    try:
        run.build()
        from analyze_stable_credit import analyze_pair
        for scenario in args.scenarios:
            for mode in ('greedy','stable'):run.arm(scenario,mode)
            result=analyze_pair(output,scenario)
            with (output/(scenario+'配对核验.json')).open('x') as stream:
                json.dump(result,stream,ensure_ascii=False,indent=2)
            run.record(阶段=scenario+'配对完成，数据/评分/维护核验通过，客机均已关闭',
                最近配对={n:result[n] for n in ('窗口内RR减少','窗口内复制减少字节','窗口内ZoneReset减少')})
        run.record(阶段='全部受控配对完成并归档，客机已全部关闭，等待解释正负结果')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__)
        raise
    finally:run.password=None


if __name__=='__main__':main()
