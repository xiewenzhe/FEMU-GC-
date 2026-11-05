#!/usr/bin/env python3
"""当前模型与最少有效量Greedy的独立热点配对；不覆盖原生db_bench试次。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import signal
import socket

from run_dbbench_parallel_host import DbRun, ROOT, IMAGE_DIR, capacity, stamp
from run_joint_host import digest_manifest
from run_tail_ablation_fixed_pair import running_parent
from ycsb_af import digest, require
from dbbench_hotspot_source import patch

LABEL='joint-dbbench8-locality-v4-20261024'
SOURCE=IMAGE_DIR/'joint-dbbench8-parallel-v2-20261023-results'
PARENT=IMAGE_DIR/'joint-dbbench8-parallel-v2-20261023-prepare.qcow2'


def prepare(args,password,output):
    out=output/'prepare';out.mkdir()
    run=DbRun(args,password,out,'prepare',8101,PARENT)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}')
        run.deployed=True
        expected=json.loads((SOURCE/'同库身份.json').read_text())
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(expected)
        require(digest_manifest(run.ssh(query,180))==expected,'原生客户端、库或模型身份漂移')
        frozen=output/'源码'
        run.ssh(f'cd /home/femu/rocksdb; nm -C librocksdb_debug.a 2>/dev/null | '
            f'grep "SyncPoint::GetInstance" > {run.target}/库调试符号诊断.log || true',180)
        run.command(run.scp()+[str(frozen/n) for n in ('hotspot.patch','rrzc_dbbench_hotspot.h')]
            +[f'femu@127.0.0.1:{run.target}/'])
        run.ssh(f'cd /home/femu/rocksdb && git apply --check {run.target}/hotspot.patch && '
            f'cp tools/db_bench_tool.cc {run.target}/原生_db_bench_tool.cc && '
            f'cp {run.target}/rrzc_dbbench_hotspot.h tools/ && git apply {run.target}/hotspot.patch && '
            f'make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench '
            f'> {run.target}/编译.log 2>&1',600)
        unchanged={k:v for k,v in expected.items() if k!='db_bench'}
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(unchanged),180))==unchanged,
                '定制客户端构建改变库或模型')
        expected['db_bench']=run.ssh('cd /home/femu/rocksdb; sha256sum db_bench').split()[0]
        for name in ('tools/db_bench_tool.cc','tools/rrzc_dbbench_hotspot.h'):
            expected[name]=run.ssh('cd /home/femu/rocksdb; sha256sum '+name).split()[0]
        stamp(output/'同库身份.json',expected)
        run.ssh(f'cd /home/femu/rocksdb; cp plugin/zenfs/fs/rrzc_direct_zenfs.inc '
            f'plugin/zenfs/fs/fs_zenfs.cc {run.target}/')
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/rrzc_direct_zenfs.inc',
                              f'femu@127.0.0.1:{run.target}/fs_zenfs.cc',str(output/'源码')])
        run.ssh(f'cd /home/femu/rocksdb && RRZC_DB_HOTSPOT=1 RRZC_DB_READ_KEYS=64 '
            f'RRZC_DB_WRITE_KEYS=960 RRZC_DB_LAYOUT_PREFIX={run.target}/本地布局 '
            f'./db_bench --db={run.target}/posix --benchmarks=fillseq,flush,rrzclayout,readwhilewriting,rrzclayout '
            '--num=2048 --writes=2048 --reads=8192 --finish_after_writes=1 --value_size=4096 '
            f'--compression_type=none --disable_wal=1 --histogram=1 --seed=20261023 > {run.target}/普通目录小测.log 2>&1',180)
        run.record(阶段='定制db_bench构建及普通目录小测通过，库和模型不变')
    except BaseException as failure:
        error=failure;run.record(原始错误=repr(failure));raise
    finally:
        try:run.finish(error)
        except Exception as cleanup:
            if error is None:raise
            run.record(收尾另有错误=str(cleanup))
    stamp(output/'准备完成.json',dict(父镜像=str(IMAGE_DIR/(args.label+'-prepare.qcow2')),
        软件身份SHA256=digest(output/'同库身份.json')))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('prepare','run'),default='prepare')
    parser.add_argument('--label',default=LABEL)
    parser.add_argument('--allow-unlocked',action='store_true')
    parser.add_argument('--detach',action='store_true')
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    require(args.label==LABEL and args.allow_unlocked,'使用独立标签并显式未锁页')
    require(PARENT.is_file() and not running_parent(PARENT),'父镜像不可用')
    budget=capacity();require(budget['资源允许并发']>=2,'两臂并行预算不足，不降低原资源保护')
    listing=__import__('subprocess').run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
    require(str(IMAGE_DIR)+'/' not in listing,'仍有本项目客机，不叠加')
    for port in (8101,8102):
        with socket.socket() as sock:require(sock.connect_ex(('127.0.0.1',port))!=0,'端口被占用')
    output=IMAGE_DIR/(args.label+'-results')
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('局部热点Greedy/模型配对认证（仅内存）：')
    if args.phase=='prepare':
        output.mkdir();frozen=output/'源码';frozen.mkdir()
        for name in ('run_dbbench_hotspot_host.py','dbbench_hotspot_source.py','rrzc_dbbench_hotspot.h'):
            shutil.copyfile(ROOT/'tests'/name,frozen/name)
        original=(SOURCE/'源码/db_bench_tool.cc').read_text()
        (frozen/'hotspot.patch').write_text(patch(original))
        stamp(output/'预注册计划.json',dict(目的='相邻稳定热读与覆盖写；Greedy及原模型并行，先核验联合机会',
            RR阈值=1024,GC启动百分比=20,SSD字节=8<<30,装载键数=1048576,资源=budget,
            读键范围='[0,4096)',热写范围='[4096,65536)，80%；其他非读热点范围20%',
            预试请求='400000读、600000写；固定种子、不限速；不是原生公开标准负载',
            选区='Greedy关闭空间/时序规划，最少有效量；模型沿用去边界联合成本，两臂共享候选及安全',
            验收='核对RR/ZC同窗、高风险候选、实际SST/extent同区；未证实则不宣称成功或直接长测',
            不变='FEMU、库、候选资格、RR触发及GC20%；只定制db_bench请求和只读映射采样'))
    else:require((output/'准备完成.json').exists(),'定制客户端准备未完成')
    if args.detach:
        reader,writer=os.pipe();pid=os.fork()
        if pid:
            os.close(writer);require(os.read(reader,1)==b'1','监督分离失败');os.close(reader)
            print(f'局部热点监督PID={pid}，目录={output}',flush=True);return
        os.close(reader);os.setsid()
        fd=os.open(output/(args.phase+'-监督.log'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
        os.write(writer,b'1');os.close(writer)
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        if args.phase=='prepare':prepare(args,password,output)
        else:
            from dbbench_hotspot_suite import execute
            execute(args,password,output,budget)
    finally:password=None


if __name__=='__main__':main()
