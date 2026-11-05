#!/usr/bin/env python3
"""全块查询、原生ZenFS接线和三场景三策略受控验证；单客机串行并收尾。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from run_rr_path_host import RrRun, preflight, PARENT, SOURCE, IMAGE_DIR, ROOT
from run_joint_host import controlled_profile, digest_manifest
from run_dbbench_parallel_host import stamp
from run_ycsb_quick_host import disk_space
from all_blocks_patch import make_patch
from ycsb_af import require, digest

LABEL='joint-all-blocks-v1-20261025'
CASES=('opportunity','stopped','low-yield')
MODES=('greedy','current','blocks')
FILES=('run_all_blocks_host.py','build_all_blocks_guest.py','run_all_blocks_guest.sh',
       'all_blocks_patch.py','all_blocks_device.cc','v1_execution_smoke_test.cc')
HEADERS=('rrzc_all_blocks.h','rrzc_all_blocks_io.h')

def run_experiment(args,password,output):
    run=RrRun(args,password,output,'blocks',8101,PARENT,retain_image=True);run.profile=controlled_profile();error=None
    run.state.update(边界='4GiB/32Zone受控功能测试；三场景三策略，非YCSB或稳态性能',GC启用=True)
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        expected=json.loads((SOURCE/'同库身份.json').read_text())
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(expected),180))==expected,
            '来源库或模型身份不符')
        for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):
            run.command(run.scp()+[f'femu@127.0.0.1:/home/femu/rocksdb/plugin/zenfs/fs/{name}',str(output/'源码'/('before-'+name))])
        patch=make_patch((output/'源码/before-fs_zenfs.cc').read_text(),(output/'源码/before-rrzc_direct_zenfs.inc').read_text())
        (output/'源码/all-blocks.patch').write_text(patch)
        upload=[output/'源码'/n for n in (*FILES,*HEADERS,'all-blocks.patch')]
        run.command(run.scp()+list(map(str,upload))+[f'femu@127.0.0.1:{run.target}/'])
        run.ssh('cd /home/femu/rocksdb/plugin/zenfs && '+
            f'git apply --check {run.target}/all-blocks.patch && git apply {run.target}/all-blocks.patch && '
            f'git apply --reverse --check {run.target}/all-blocks.patch && '+
            f'cp {run.target}/rrzc_all_blocks.h {run.target}/rrzc_all_blocks_io.h fs/')
        run.record(阶段='只编译新ZenFS对象及受控客户端；不重建静态库')
        run.ssh(f'python3 {run.target}/build_all_blocks_guest.py {run.target}',300)
        for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):
            run.command(run.scp()+[f'femu@127.0.0.1:/home/femu/rocksdb/plugin/zenfs/fs/{name}',str(output/'源码'/('after-'+name))])
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/对象优先链接核验.json',str(output/'对象优先链接核验.json')])
        unchanged={k:v for k,v in expected.items() if k not in ('plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/rrzc_direct_zenfs.inc')}
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(unchanged),180))==unchanged,
            '修改了RR、旧模型或原静态库')
        for case in args.cases:
            for mode in MODES:
                disk_space();tag=case+'-'+mode
                run.record(阶段=tag+'：全空盘重新初始化、真实读历史与原生GC/RR')
                run.root(f'nohup setsid bash {run.target}/run_all_blocks_guest.sh {case} {mode} '
                    f'< /dev/null > {run.target}/{tag}.启动.log 2>&1 & echo $! > {run.target}/{tag}.pid')
                started=time.monotonic()
                while time.monotonic()-started<180:
                    disk_space()
                    reply=run.ssh(f'if test -f {run.target}/{tag}/finished; then cat {run.target}/{tag}/wrapper.exit; '
                        f'else echo 运行中; fi; test ! -f {run.target}/{tag}/run.log || tail -1 {run.target}/{tag}/run.log; true')
                    run.record(当前场景=case,当前策略=mode,最近观测=reply)
                    print(tag+'：'+reply.strip().replace('\n','；'),flush=True)
                    if reply.splitlines()[0]!='运行中':
                        require(reply.splitlines()[0]=='0',tag+'失败，归档现场后正常关机');break
                    time.sleep(5)
                else:raise RuntimeError(tag+'超过3分钟，停止追加负载，归档关机')
        run.record(阶段='三场景配对完成，归档源码和原始日志后正常关机')
    except BaseException as failure:error=failure;run.record(原始错误=repr(failure));raise
    finally:
        try:run.finish(error)
        except Exception as cleanup:
            if error is None:raise
            run.record(收尾另有错误=repr(cleanup))
    subprocess.run([sys.executable,str(output/'源码/analyze_all_blocks.py'),
        str(output/args.label),str(output/'分析')],check=True)
    run.record(阶段='三策略受控业务、独立成本及选择核验完成，客机已退出')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true')
    p.add_argument('--label',default=LABEL);p.add_argument('--cases',nargs='+',choices=CASES,default=list(CASES));args=p.parse_args()
    import re
    require(re.fullmatch(r'joint-[a-z0-9-]+',args.label) is not None,'非法实验标签')
    require(len(set(args.cases))==len(args.cases),'场景重复')
    budget=preflight();output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖已有实验')
    # 仅新增一个小型对象、一个去调试符号工具和少量日志；不采用6GiB整库构建路径。
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('全块受控实验认证（仅内存）：')
    output.mkdir();frozen=output/'源码';frozen.mkdir()
    for name in FILES:shutil.copyfile(ROOT/'tests'/name,frozen/name)
    for name in HEADERS:shutil.copyfile(ROOT/'model'/name,frozen/name)
    shutil.copyfile(ROOT/'analyze_all_blocks.py',frozen/'analyze_all_blocks.py')
    shutil.copyfile(ROOT/'tests/gc_path_trace.py',frozen/'gc_path_trace.py')
    stamp(output/'预注册计划.json',dict(目的='验证低RC快读Block的风险识别及其对真实选区和维护的影响',
        场景=args.cases,策略=MODES,设备='4GiB/32Zone、每Zone128MiB/128块、严格RR1024、GC20%、未锁页',
        父镜像=str(PARENT),原库身份=str(SOURCE/'同库身份.json'),FEMU_SHA256=digest(ROOT.parents[1]/'build-femu/qemu-system-x86_64'),
        资源=budget,编译='只编译fs_zenfs.cc并优先链接，检查未抽取原库旧对象；原静态库不变',
        初始化='同一隔离客机逐臂Reset全盘，重新写入和预读；不声称qcow2保存了RC',
        对照='同一场景相同文件、预读计数、读取偏移及有限定时输入；控制轨迹不是限速性能测试',
        边界='候选有效量稳定，先隔离全块风险差异；不声称已验证真实SST退役预测或目的区长期RR净收益',
        指标='业务截止前RR/ZC、迁移统计、源Reset及结束空闲；内容核验单独划界；不报告性能提升',
        收尾='必要原始日志与源码归档，哈希核验后正常关机，保留子镜像'))
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:run_experiment(args,password,output)
    finally:password=None

if __name__=='__main__':main()
