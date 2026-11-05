#!/usr/bin/env python3
"""候选范围实验：独立编译、三类场景三组串行；每阶段归档后正常关机。"""
import argparse
import getpass
import json
from pathlib import Path
import signal
import sys
import run_joint_host as base
from run_joint_host import require,PROJECT,IMAGE_DIR,digest_manifest
from run_strict_rr_build_host import StrictBuild
from run_window_credit_host import WindowRun,resources,digest
from analyze_candidate_pool import CASES,MODES,analyze_arm,compare_three

LABEL='joint-candidate-pool-v1-20261022'
SOURCE=IMAGE_DIR/'joint-strict-ycsb-v3-20261022-results'
PARENT=IMAGE_DIR/'joint-strict-ycsb-v3-20261022-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
ROOT=PROJECT/'research/rrzc';TESTS=ROOT/'tests'
FILES=[TESTS/n for n in ('build_candidate_pool_guest.sh','run_candidate_pool_guest.sh',
    'candidate-pool-client.patch','joint_opportunity_smoke.cc','v1_execution_smoke_test.cc',
    'joint_read_trace.h','test_candidate_pool.cc','strict_stats_guest.py')]
FILES += [ROOT/'model/rrzc_candidate_pool.h',ROOT/'zenfs-candidate-pool.patch']


class PoolRun(StrictBuild):
    # 不部署YCSB采证器；本轮程序将日志直接放到独立结果目录。
    collect=WindowRun.collect

    def freeze(self):
        frozen=self.output/'源码';frozen.mkdir()
        for p in [*FILES,Path(__file__).resolve()]:(frozen/p.name).write_bytes(p.read_bytes())
        for directory,pattern,title in ((ROOT,'*.py','分析'),(ROOT/'model','*.h','模型'),(TESTS,'*.py','监督')):
            target=frozen/title;target.mkdir()
            for p in directory.glob(pattern):(target/p.name).write_bytes(p.read_bytes())
        (frozen/'身份.json').write_text(json.dumps({str(p.relative_to(PROJECT)):digest(p)
            for p in [*FILES,Path(__file__).resolve()]},ensure_ascii=False,indent=2))
        return frozen

    def build(self):
        frozen=self.freeze();out=self.output/'build';out.mkdir();expected=json.loads(MANIFEST.read_text())
        try:
            self.start('build')
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120)
            require(digest_manifest(raw)==expected,'父镜像实际软件身份不符')
            (frozen/'parent.software.sha256').write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            self.command(self.scp()+[str(frozen/p.name) for p in FILES]+
                [str(frozen/'parent.software.sha256'),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_candidate_pool_guest.sh '
                f'< /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='隔离编译候选范围开关；旧模型及严格RR不变')
            self.wait_for(self.target+'/build.finished',
                f'test ! -f {self.target}/library-build.log || tail -1 {self.target}/library-build.log',1200)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','构建失败，已保留原始日志并关闭客机')
        actual=digest_manifest((built/'software.sha256').read_text())
        changed={'plugin/zenfs/fs/fs_zenfs.cc','librocksdb_debug.a'}
        require(all(actual.get(k)==v for k,v in expected.items() if k not in changed),'旧模型、RR或旧程序身份改变')
        require(actual['plugin/zenfs/fs/rrzc_candidate_pool.h']==digest(frozen/'rrzc_candidate_pool.h'),
            '部署候选范围核与冻结源码不同')
        (self.output/'同库身份.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2))
        self.state['已完成'].append('build');self.record(阶段='整库、候选边界及新受控程序编译通过，客机已退出')

    def arm(self,scenario,mode):
        require(scenario in CASES and mode in MODES,'未登记的受控组')
        self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        tag=scenario+'-'+mode;out=self.output/tag;out.mkdir()
        remote='/home/femu/rrzc-joint-results/'+scenario
        expected=json.loads((self.output/'同库身份.json').read_text())
        try:
            self.start(tag)
            require(digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120))==expected,
                '运行前同库软件身份不符')
            self.root(f'nohup setsid bash {self.target}/run_candidate_pool_guest.sh {mode} {scenario} '
                f'< /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'真实受控运行，未注入RC、不强制选区',客机结果目录=remote)
            self.wait_for(remote+'/finished',
                f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',360)
        finally:self.finish(out,remote)
        result=analyze_arm(out/scenario,mode,scenario)
        (out/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.state['已完成'].append(tag);self.record(阶段=tag+'载荷、评分及严格RR验收通过，客机已关闭',
            最近结果={k:result[k] for k in ('RR次数','ZC次数','总复制字节','总成功ZoneReset')})
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true');parser.add_argument('--label',default=LABEL)
    parser.add_argument('--cases',nargs='+',choices=CASES,default=list(CASES))
    args=parser.parse_args();args.phase='build';args.scenarios=[]
    require(len(set(args.cases))==len(args.cases),'场景重复')
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(args);before=resources(profile)
    state=json.loads((SOURCE/'状态.json').read_text())
    require(state['当前客机PID'] is None and '全部客机关闭' in state['阶段'],'严格RR来源尚未完成收尾')
    require(digest(PROJECT/'build-femu/qemu-system-x86_64')==
        json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'],'严格FEMU版本漂移')
    for case in args.cases:
        for mode in MODES:
            tag=args.label+'-'+case+'-'+mode
            for p in (IMAGE_DIR/(tag+'.qcow2'),Path('/tmp')/(tag+'.pid'),
                    Path('/tmp')/(tag+'.qmp'),Path('/tmp')/(tag+'-ssh.sock')):
                require(not p.exists(),'目标已存在，拒绝覆盖：'+str(p))
    if args.check:print(json.dumps(dict(预检通过=True,资源=before),ensure_ascii=False,indent=2));return
    password=getpass.getpass('候选范围隔离实验认证（仅内存）：')
    output.mkdir();(output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    plan=dict(目的='分离候选范围扩大与RR评分的真实维护效果，不预设正收益',父镜像=str(PARENT),
        软件来源=str(MANIFEST),FEMU二进制SHA256=digest(PROJECT/'build-femu/qemu-system-x86_64'),
        场景=args.cases,策略=list(MODES),初始资源=before,
        保持='4GiB/32区/128MiB区/128块每区；RR1024严格同步、GC20%、满区与正回收、安全路径、模型公式不变',
        输入='冷源4MiB，热源5MiB；低空间收益热源120MiB；真实同块预读928次，后续128次16/s，停读场景0次',
        初态='同库同程序，各臂新NAND真实写入和预读，核对源映射/代际/真实RC；不以qcow2冒充内存快照',
        观察='释放后固定25秒，真实完成次数与逐文件复制闭合；候选全量载荷、填充文件首尾校验；非YCSB',
        收尾='串行一台约8GiB、额外16GiB余量；成功失败均完整文件哈希归档、正常关机及PID/SSH核验',
        后续='三场景验收并分析后再决定YCSB三组，不自动扩大规模或同时更改读速率估计')
    (output/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    run=PoolRun(args,password,output,profile);run.qemu_digest=plan['FEMU二进制SHA256']
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        run.build()
        for case in args.cases:
            arms={mode:run.arm(case,mode) for mode in MODES}
            result=compare_three(arms)
            (output/(case+'三组核验.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
            run.record(阶段=case+'三组验收完成，全部客机关闭',最近差值=result['比较'])
        run.record(阶段='候选范围所请求受控三组完成，全部客机关闭，未运行新YCSB')
    except BaseException as error:
        run.record(阶段='候选范围实验停止，原始错误和归档保留，不自动重跑',错误=str(error));raise
    finally:run.password=None

if __name__=='__main__':main()
