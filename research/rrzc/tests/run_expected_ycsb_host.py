#!/usr/bin/env python3
"""冻结已验收期望RR库并重链接YCSB，串行小测与40万请求配对，失败正常收尾。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import signal

import run_joint_host as base
from run_joint_host import require,PROJECT,IMAGE_DIR
from run_window_credit_host import resources,digest
from run_window_ycsb_host import WindowYcsbRun,FILES as WINDOW_FILES,STAGES
from run_sst_probe_host import CLIENT_MANIFEST
from analyze_expected_rr import analyze_pair as controlled_pair
from analyze_expected_ycsb import analyze_arm,compare_pair

LABEL='joint-expected-ycsb-v1-20261022'
SOURCE=IMAGE_DIR/'joint-expected-rr-v1-20261022-results'
PARENT=IMAGE_DIR/'joint-expected-rr-v1-20261022-build.qcow2'
MANIFEST=SOURCE/'同库身份.json'
ROOT=PROJECT/'research/rrzc';TESTS=ROOT/'tests'
CASES=('burst-stopped','eligible-cost','retire')
MODES=('greedy','expected')
FILES=[p for p in WINDOW_FILES if p.name not in
       ('build_window_ycsb_guest.sh','run_window_ycsb_guest.sh','capture_window_ycsb_guest.py')]
FILES += [TESTS/n for n in ('build_expected_ycsb_guest.sh','run_expected_ycsb_guest.sh','capture_expected_ycsb_guest.py')]


def checked_source():
    state=json.loads((SOURCE/'状态.json').read_text())
    require(state['当前客机PID'] is None and '三个真实功能配对完成' in state['阶段'],'源功能配对未完成退出')
    for tag in ('build',*[c+'-'+m for c in CASES for m in MODES]):
        require(tag in state['已完成'],'源阶段未完成：'+tag)
        archive=json.loads((SOURCE/tag/'阶段归档.json').read_text())
        require(archive['完整'] and not archive['客机仍存活'],'源阶段归档或关机不完整：'+tag)
    expected=json.loads(MANIFEST.read_text())
    plan=json.loads((SOURCE/'预注册计划.json').read_text())
    require(digest(PROJECT/'build-femu/qemu-system-x86_64')==plan['FEMU二进制SHA256'],'FEMU版本漂移')
    require(json.loads((SOURCE/'平台配置.json').read_text())==base.controlled_profile(),'设备配置漂移')
    for p in (ROOT/'model').glob('*.h'):
        key='plugin/zenfs/fs/'+p.name
        if key in expected:require(digest(p)==expected[key],'冻结模型头变化：'+p.name)
    return expected,{c:controlled_pair(SOURCE,c) for c in CASES}


class ExpectedYcsbRun(WindowYcsbRun):
    files=FILES
    manifest=MANIFEST
    modes=MODES
    capture_adapter='capture_expected_ycsb_guest.py'
    build_script='build_expected_ycsb_guest.sh'
    run_script='run_expected_ycsb_guest.sh'
    title='期望RR'
    analyze=staticmethod(analyze_arm)
    extra_dependencies=(Path(__file__).resolve(),)

    def freeze_extra(self,frozen):
        target=frozen/'模型';target.mkdir()
        for p in (ROOT/'model').glob('*.h'):(target/p.name).write_bytes(p.read_bytes())
        source=TESTS/'test_expected_rr_selector.cc'
        (frozen/source.name).write_bytes(source.read_bytes())

    def arm(self,stage,mode):
        for p in (self.output/'源码/模型').glob('*.h'):
            require(digest(p)==digest(ROOT/'model'/p.name),'宿主模型漂移，不能继续验收')
        require(digest(self.output/'源码/test_expected_rr_selector.cc')==digest(TESTS/'test_expected_rr_selector.cc'),
            '独立核验桥接程序漂移')
        super().arm(stage,mode)


def interrupted(*_):raise KeyboardInterrupt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',default=LABEL)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();args.phase='build';args.scenarios=[]
    base.PARENT=PARENT;base.PARENT_MANIFEST=MANIFEST
    output,profile=base.preflight(args);before=resources(profile);_,source=checked_source()
    require(CLIENT_MANIFEST.is_file(),'缺原YCSB源码身份')
    for s in STAGES:
        for m in MODES:
            for p in (IMAGE_DIR/f'{args.label}-{s}-{m}.qcow2',Path(f'/tmp/{args.label}-{s}-{m}.pid'),
                Path(f'/tmp/{args.label}-{s}-{m}.qmp'),Path(f'/tmp/{args.label}-{s}-{m}-ssh.sock')):
                require(not p.exists(),'目标已存在，拒绝覆盖：'+str(p))
    if args.check:
        print(json.dumps(dict(只读预检通过=True,资源=before,三类功能配对重新核验通过=True),ensure_ascii=False));return
    password=getpass.getpass('期望RR的YCSB隔离客机认证（仅驻留内存）：')
    output.mkdir();binary=digest(PROJECT/'build-femu/qemu-system-x86_64')
    (output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'源功能配对复核.json').write_text(json.dumps(source,ensure_ascii=False,indent=2))
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='冻结期望RR模型，真实不限速YCSB检验总维护及前台性能，不继续调参',
        父镜像=str(PARENT),软件来源=str(MANIFEST),FEMU二进制SHA256=binary,初始资源=before,
        阶段=['build',*[s+'-'+m for s in STAGES for m in MODES]],
        负载='单线程、种子20261017、读更新各50%、4KiB字段；小测8192键/8192请求；短测491520键/400000请求',
        键范围='短测读[0,4096)、更新[0,65536)，不保证物理同区',
        限速='limit.ops=0且无limit.file；无WAL、无块缓存',
        不变='4GiB功能盘/32个128MiB区/128块每区/RR1024/GC20%/原候选资格/回收门槛0/共享RR公平队列及安全复核',
        唯一差异='相同整库及客户端；Greedy零抵扣，expected直接期望净复制/回收排序；泊松假设未校准',
        验收='静态重链接及四次普通目录自测；完整全键校验、独立CDF评分、维护与Reset代际账目',
        指标='RR/ZC/迁移/Reset及映射推导块擦除、API均值和P99/P99.9/P99.99、Run及前台吞吐、末态Zone状态',
        监督='单臂1500秒、请求180秒无进展即采证关闭；失败不自动重跑，串行一台，归档哈希后正常关机',
        边界='不是32GiB论文规模或稳态；不同新NAND不是相同内存快照，单轮不证明稳定因果收益'),ensure_ascii=False,indent=2))
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
    run=ExpectedYcsbRun(args,password,output,profile);run.qemu_digest=binary
    try:
        run.build()
        for stage in STAGES:
            for mode in MODES:run.arm(stage,mode)
            result=compare_pair(output,stage)
            with (output/(stage+'配对核验.json')).open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
            run.record(阶段=stage+'期望RR配对验收通过且两臂已退出',最近差值=result['模型相对Greedy差值'])
        run.record(阶段='期望RR的YCSB小测及40万请求配对完成，全部客机关闭，未自动长测')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__);raise
    finally:run.password=None


if __name__=='__main__':main()
