#!/usr/bin/env python3
"""新子镜像编译期望RR分支，串行Greedy配对；失败归档正常关机，不自动重跑或长测。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import signal

import run_joint_host as base
from run_joint_host import require,PROJECT,IMAGE_DIR,digest_manifest
from run_persistent_credit_host import PersistentRun
from run_window_credit_host import resources,digest
from analyze_expected_rr import analyze_arm,analyze_pair
from analyze_expected_rr_probe import analyze as probe_analysis

LABEL='joint-expected-rr-v1-20261022'
SOURCE=IMAGE_DIR/'joint-rr-probability-probe-v1-20261022-results'
PARENT=IMAGE_DIR/'joint-rr-probability-probe-v1-20261022-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
ROOT=PROJECT/'research/rrzc';TESTS=ROOT/'tests'
CASES=('burst-stopped','eligible-cost','retire')
MODES=('greedy','expected')
FILES=[TESTS/n for n in ('build_expected_rr_guest.sh','run_expected_rr_guest.sh',
    'joint_opportunity_smoke.cc','v1_execution_smoke_test.cc','joint_read_trace.h','test_expected_rr_selector.cc')]
FILES += [ROOT/'model/rrzc_expected_rr_selector.h',ROOT/'model/rrzc_recent_sampler.h',ROOT/'zenfs-expected-rr.patch']


def checked_source():
    state=json.loads((SOURCE/'状态.json').read_text())
    require(state['当前客机PID'] is None and '三个Greedy只读采证完成' in state['阶段'],'原采证未完成或客机仍驻留')
    expected=json.loads(MANIFEST.read_text())
    for tag in ('build',*[c+'-greedy' for c in CASES]):
        require(tag in state['已完成'],'缺原采证阶段：'+tag)
        out=SOURCE/tag;phase=json.loads((out/'阶段归档.json').read_text())
        require(phase['完整'] and not phase['客机仍存活'],'原阶段未完整归档退出：'+tag)
    for case in CASES:
        out=SOURCE/(case+'-greedy');folder=out/case
        manifest=json.loads((out/(case+'.归档核验.json')).read_text())
        actual={str(p.relative_to(folder)):digest(p) for p in folder.rglob('*') if p.is_file()}
        require(manifest['核验通过'] and actual==manifest['文件SHA256'],'原采证归档哈希变化')
        current=probe_analysis(folder,case)
        require(current==json.loads((out/'概率采证核验.json').read_text()),'原同块采证重新核验不一致')
    plan=json.loads((SOURCE/'预注册计划.json').read_text())
    require(digest(PROJECT/'build-femu/qemu-system-x86_64')==plan['FEMU二进制SHA256'],'FEMU身份变化')
    require(json.loads((SOURCE/'平台配置.json').read_text())==base.controlled_profile(),'功能盘配置变化')
    for name in ('rrzc_persistent_credit_selector.h','rrzc_window_credit_selector.h','rrzc_v1_growth.h'):
        require(digest(ROOT/'model'/name)==expected['plugin/zenfs/fs/'+name],'旧模型或同块核验头变化：'+name)
    return expected


class ExpectedRun(PersistentRun):
    def build(self):
        frozen=self.output/'源码';frozen.mkdir()
        dependencies=[Path(__file__).resolve(),TESTS/'run_persistent_credit_host.py',TESTS/'run_window_credit_host.py',
            TESTS/'run_joint_host.py',TESTS/'run_rr_fair_long_v2_host.py',TESTS/'run_tail_ablation_fixed_pair.py',
            TESTS/'run_tail_ablation_smoke_pair.py',TESTS/'run_stream_pair.py']
        for p in FILES+dependencies:(frozen/p.name).write_bytes(p.read_bytes())
        for directory,pattern,title in ((ROOT,'*.py','分析'),(ROOT/'model','*.h','模型')):
            target=frozen/title;target.mkdir()
            for p in directory.glob(pattern):(target/p.name).write_bytes(p.read_bytes())
        identities={str(p.relative_to(PROJECT)):digest(p) for p in FILES+dependencies}
        with (frozen/'身份.json').open('x') as stream:json.dump(identities,stream,ensure_ascii=False,indent=2)
        expected=json.loads(MANIFEST.read_text());out=self.output/'build';out.mkdir()
        try:
            self.start('build')
            raw=self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120)
            require(digest_manifest(raw)==expected,'实际父镜像软件与完整采证身份不同')
            manifest=frozen/'parent.software.sha256';manifest.write_text(raw)
            self.ssh(f'test ! -e {self.target} && mkdir {self.target}')
            self.command(self.scp()+[str(frozen/p.name) for p in FILES]+[str(manifest),f'femu@127.0.0.1:{self.target}/'])
            self.ssh(f'nohup setsid bash {self.target}/build_expected_rr_guest.sh < /dev/null > {self.target}/构建启动.log 2>&1 &')
            self.record(阶段='新子镜像编译期望RR核、整库及同一受控程序，旧评分和RR路径不变')
            self.wait_for(self.target+'/build.finished',f'test ! -f {self.target}/library-build.log || tail -1 {self.target}/library-build.log',1200)
        finally:self.finish(out)
        built=out/Path(self.target).name
        require((built/'build.exit').read_text().strip()=='0','新分支构建失败，已归档，不自动重跑')
        software=digest_manifest((built/'software.sha256').read_text())
        changed={'plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc','librocksdb_debug.a'}
        require(all(software.get(n)==v for n,v in expected.items() if n not in changed),'旧模型、RR或旧客户端意外改变')
        require(software['librocksdb_debug.a']!=expected['librocksdb_debug.a'],'未确认新整库重编译')
        require(software['plugin/zenfs/fs/rrzc_expected_rr_selector.h']==digest(frozen/'rrzc_expected_rr_selector.h'),
            '部署新核与冻结原型不同')
        with (self.output/'同库身份.json').open('x') as stream:json.dump(software,stream,ensure_ascii=False,indent=2)
        self.state['已完成'].append('build');self.record(阶段='新整库及客户端编译、旧身份核验通过，构建客机已归档退出')

    def verify_host_model(self):
        frozen=self.output/'源码'
        for p in (frozen/'模型').glob('*.h'):
            require(digest(p)==digest(ROOT/'model'/p.name),'运行期间宿主模型头漂移，停止验收')
        require(digest(frozen/'test_expected_rr_selector.cc')==digest(TESTS/'test_expected_rr_selector.cc'),
            '运行期间宿主评分桥接程序漂移')

    def arm(self,scenario,mode):
        require(scenario in CASES and mode in MODES,'未知配对场景或策略')
        tag=scenario+'-'+mode;self.parent_image=IMAGE_DIR/f'{self.label_prefix}-build.qcow2'
        out=self.output/tag;out.mkdir();remote='/home/femu/rrzc-joint-results/'+scenario
        expected=json.loads((self.output/'同库身份.json').read_text());error=None
        try:
            self.start(tag)
            require(digest_manifest(self.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),120))==expected,
                '两臂实际软件与同库身份不符')
            self.root(f'nohup setsid bash {self.target}/run_expected_rr_guest.sh {mode} {scenario} '
                f'< /dev/null > {self.target}/{tag}.启动.log 2>&1 &')
            self.record(阶段=tag+'：真实选区、原读取及原生ZC/RR，不是YCSB',客机结果目录=remote)
            self.wait_for(remote+'/finished',f'test ! -f {remote}/run.stdout.log || tail -1 {remote}/run.stdout.log',300)
        except BaseException as cause:
            error=cause;self.record(原始业务或监督错误=type(cause).__name__+'：'+str(cause));raise
        finally:
            try:self.finish(out,remote)
            except Exception as cleanup:
                if error is None:raise
                self.record(收尾另有错误=str(cleanup))
        self.verify_host_model();result=analyze_arm(out/scenario,mode,scenario)
        with (out/'独立核验.json').open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
        self.state['已完成'].append(tag)
        self.record(阶段=tag+'实际评分、载荷和维护账目通过，已归档正常关机',
            最近结果={k:result[k] for k in ('ZC次数','RR次数','总复制字节','总成功ZoneReset')},
            实际改选=len(result['相对同轮Greedy改选']))


def interrupted(*_):raise KeyboardInterrupt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',default=LABEL)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();args.phase='build';args.scenarios=[]
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(args);before=resources(profile);checked_source()
    for case in CASES:
        for mode in MODES:
            for p in (IMAGE_DIR/f'{args.label}-{case}-{mode}.qcow2',Path(f'/tmp/{args.label}-{case}-{mode}.pid'),
                Path(f'/tmp/{args.label}-{case}-{mode}.qmp'),Path(f'/tmp/{args.label}-{case}-{mode}-ssh.sock')):
                require(not p.exists(),'目标已存在，拒绝覆盖：'+str(p))
    if args.check:print(json.dumps(dict(只读预检通过=True,未启动客机=True,资源=before),ensure_ascii=False));return
    password=getpass.getpass('期望RR真实配对客机认证（仅驻留内存）：')
    output.mkdir();(output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    plan=dict(目的='把新模型真实用于victim选择，验证总维护成本而不预设正收益',父镜像=str(PARENT),
        FEMU二进制SHA256=digest(PROJECT/'build-femu/qemu-system-x86_64'),软件来源=str(MANIFEST),
        场景=list(CASES),策略=list(MODES),初始资源=before,
        不变='4GiB/RR1024/GC20%/原候选资格/固定读取/原迁移与共享RR公平队列/旧模型及旧客户端',
        新分支='独立默认关闭开关；直接概率乘留存的净复制/回收排序，不先选Greedy、不加权重',
        证据='同一库、程序和500毫秒只读轨迹；每臂新建NAND并核对真实映射/RC/代际/请求计划',
        验收='完整编译、实际配置、独立十进制CDF、字节账目、真实victim、载荷、维护账目及完整归档',
        退役边界='突然删除无提前可见信号，允许新机制额外搬运；不使用未来文件失效时刻评分',
        收尾='串行一台，文件集合/SHA256归档后正常关机及PID/SSH退出；失败停止，不强杀或自动重跑',
        后续='三个功能配对之后再评估是否进入重复不限速YCSB；本入口不自动长测或修改公式')
    (output/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    if args.detach:
        read,write=os.pipe();pid=os.fork()
        if pid:
            os.close(write);require(os.read(read,1)==b'1','监督分离失败');os.close(read)
            print(f'监督PID={pid}，状态={output / "状态.json"}',flush=True);return
        os.close(read);os.setsid()
        fd=os.open(output/'监督.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
        os.write(write,b'1');os.close(write)
    signal.signal(signal.SIGTERM,interrupted)
    run=ExpectedRun(args,password,output,profile);run.qemu_digest=plan['FEMU二进制SHA256']
    run.state['模式顺序']=[c+'-'+m for c in CASES for m in MODES]
    try:
        run.build()
        for case in CASES:
            for mode in MODES:run.arm(case,mode)
            run.verify_host_model();result=analyze_pair(output,case)
            with (output/(case+'配对核验.json')).open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
            run.record(阶段=case+'真实配对完成且客机已退出，不预设正收益',
                最近差值={k:result[k] for k in ('窗口内RR减少','窗口内复制减少字节','窗口内ZoneReset减少')})
        run.record(阶段='期望RR三个真实功能配对完成，全部客机关闭，未运行新YCSB')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__);raise
    finally:run.password=None


if __name__=='__main__':main()
