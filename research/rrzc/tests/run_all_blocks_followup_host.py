#!/usr/bin/env python3
"""固定追加写入，验证联合维护短窗收益是否被后续ZC抵消；单台串行。"""
import argparse
import getpass
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from run_rr_path_host import RrRun,preflight,IMAGE_DIR,ROOT
from run_gc_path_host import SOURCE
from run_joint_host import controlled_profile,digest_manifest
from run_tail_ablation_fixed_pair import running_parent
from run_dbbench_parallel_host import stamp
from run_ycsb_quick_host import disk_space
from all_blocks_followup import client_source,wrapper_source
from ycsb_af import digest,require

LABEL='joint-all-blocks-space-v1-20261025'
OLD=IMAGE_DIR/'joint-all-blocks-v4-20261025-results'
PARENT=IMAGE_DIR/'joint-all-blocks-v4-20261025-blocks.qcow2'
CASES=('keep','retire');MODES=('greedy','blocks')
FILES=('run_all_blocks_followup_host.py','all_blocks_followup.py','build_all_blocks_followup_guest.py',
       'build_all_blocks_guest.py','v1_execution_smoke_test.cc')

def identity():
    expected=json.loads((SOURCE/'同库身份.json').read_text())
    for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):
        expected['plugin/zenfs/fs/'+name]=digest(OLD/'源码'/('after-'+name))
    for name in ('rrzc_all_blocks.h','rrzc_all_blocks_io.h'):
        expected['plugin/zenfs/fs/'+name]=digest(OLD/'源码'/name)
    verified=json.loads((OLD/'对象优先链接核验.json').read_text())
    expected['librocksdb_debug.a']=verified['原库SHA256']
    expected['/home/femu/joint-all-blocks-v4-20261025/fs_all_blocks.o']=verified['新对象SHA256']
    return expected

def execute(args,password,output):
    run=RrRun(args,password,output,'followup',8101,PARENT);run.profile=controlled_profile();error=None
    run.state.update(边界='4GiB受控后续空间实验；不是稳态或性能实验',GC启用=True)
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        expected=identity()
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(expected),180))==expected,
            '真实父软件与验收对象不符')
        upload=[p for p in (output/'源码').iterdir() if p.is_file()]
        run.command(run.scp()+list(map(str,upload))+[f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='只链接后续客户端，复用原模型与原库')
        run.ssh(f'python3 {run.target}/build_all_blocks_followup_guest.py {run.target}',180)
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/复用模型核验.json',str(output/'复用模型核验.json')])
        for case in CASES:
            for mode in MODES:
                disk_space();tag=case+'-'+mode
                run.record(阶段=tag+'：相同初始读取、追加128MiB，再等待原生GC',当前场景=case,当前策略=mode)
                run.root(f'nohup setsid bash {run.target}/run_all_blocks_guest.sh {case} {mode} '
                    f'< /dev/null > {run.target}/{tag}.启动.log 2>&1 & echo $! > {run.target}/{tag}.pid')
                started=time.monotonic()
                while time.monotonic()-started<180:
                    disk_space()
                    reply=run.ssh(f'if test -f {run.target}/{tag}/finished; then cat {run.target}/{tag}/wrapper.exit; '
                        f'else echo 运行中; fi; test ! -f {run.target}/{tag}/run.log || tail -1 {run.target}/{tag}/run.log; true')
                    run.record(最近观测=reply);print(tag+'：'+reply.strip().replace('\n','；'),flush=True)
                    if reply.splitlines()[0]!='运行中':require(reply.splitlines()[0]=='0',tag+'失败，采证关机');break
                    time.sleep(5)
                else:raise RuntimeError(tag+'超过3分钟，不追加工作量，归档关机')
        run.record(阶段='四臂后续实验完成，归档并正常关机')
    except BaseException as failure:error=failure;run.record(原始错误=repr(failure));raise
    finally:
        try:run.finish(error)
        except Exception as cleanup:
            if error is None:raise
            run.record(收尾另有错误=repr(cleanup))
    run.record(阶段='后续实验已归档且客机退出，开始独立核验')
    subprocess.run([sys.executable,str(output/'源码/analyze_all_blocks_followup.py'),
        str(output/args.label),str(output/'分析')],check=True)
    run.record(阶段='四臂后续业务与独立核验完成，客机已退出',已完成=[c+'-'+m for c in CASES for m in MODES])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');p.add_argument('--label',default=LABEL);args=p.parse_args()
    import re
    require(re.fullmatch(r'joint-[a-z0-9-]+',args.label) is not None,'非法标签')
    budget=preflight();require(PARENT.is_file() and not running_parent(PARENT),'模型父镜像正在使用或缺失')
    require(digest(ROOT.parents[1]/'build-femu/qemu-system-x86_64')==json.loads((OLD/'预注册计划.json').read_text())['FEMU_SHA256'],
        'FEMU与已验收全块接口版本不同')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧结果')
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('后续空间实验认证（仅内存）：');output.mkdir();frozen=output/'源码';frozen.mkdir()
    for name in FILES:shutil.copyfile(ROOT/'tests'/name,frozen/name)
    (frozen/'all_blocks_device.cc').write_text(client_source((ROOT/'tests/all_blocks_device.cc').read_text()))
    (frozen/'run_all_blocks_guest.sh').write_text(wrapper_source((ROOT/'tests/run_all_blocks_guest.sh').read_text()))
    (frozen/'expected.json').write_text(json.dumps(identity(),ensure_ascii=False,indent=2))
    for name in ('analyze_all_blocks_followup.py','analyze_all_blocks.py'):shutil.copyfile(ROOT/name,frozen/name)
    shutil.copyfile(ROOT/'tests/gc_path_trace.py',frozen/'gc_path_trace.py')
    stamp(output/'预注册计划.json',dict(目的='区分真正减少复制与把RR替换成稍后ZC',场景=CASES,策略=MODES,
        平台='4GiB/32Zone/128MiB每Zone，严格RR1024、GC20%、未锁页',父镜像=str(PARENT),资源=budget,
        负载='原机会场景相同初始读写；25秒窗口后冷文件保留或双方同位置删除；各追加4×32MiB相同文件，等待12秒原生GC',
        验证='原模型对象和库哈希不变、原生链接、完整内容、逐阶段空间、追加写入一致、实际复制及Reset',
        边界='冷数据退役是受控业务条件，未来安排不输入选区；不强制GC或按组改变工作量；仍非稳态性能',
        收尾='单客机串行、成功和失败都归档并正常关机；不保存内存NAND快照'))
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:execute(args,password,output)
    finally:password=None

if __name__=='__main__':main()
