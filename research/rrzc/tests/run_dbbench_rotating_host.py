#!/usr/bin/env python3
"""独立编译定制轮转db_bench，原模型Greedy串行配对，保护原磁盘余量。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
from types import SimpleNamespace

from run_dbbench_parallel_host import DbRun,ROOT,IMAGE_DIR,capacity,stamp
from run_tail_ablation_fixed_pair import running_parent
from run_joint_host import digest_manifest
from run_ycsb_quick_host import disk_space
from ycsb_af import digest,require
from dbbench_hotspot_source import patched as hot_patched
from dbbench_hotspot_suite import worker as hot_worker,module,layout
from dbbench_rotating_source import patch
from dbbench_rotating_suite import READS,WRITES,freeze,trace

LABEL='joint-dbbench8-rotating-v2-20261024'
SOURCE=IMAGE_DIR/'joint-dbbench8-locality-v4-20261024-results'
PARENT=IMAGE_DIR/'joint-dbbench8-locality-v4-20261024-prepare.qcow2'
NATIVE=IMAGE_DIR/'joint-dbbench8-parallel-v2-20261023-results/源码/db_bench_tool.cc'


def preflight():
    require(PARENT.is_file() and not running_parent(PARENT),'软件父镜像不可用')
    listing=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
    require(str(IMAGE_DIR)+'/' not in listing,'仍有本项目客机，不叠加')
    with socket.socket() as sock:require(sock.connect_ex(('127.0.0.1',8101))!=0,'8101被占用')
    return capacity()


def prepare(args,password,output):
    out=output/'prepare';out.mkdir();run=DbRun(args,password,out,'prepare',8101,PARENT);error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        expected=json.loads((SOURCE/'同库身份.json').read_text())
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(expected),180))==expected,
                '父镜像中的客户端、库或模型身份漂移')
        run.command(run.scp()+[str(output/'源码'/n) for n in ('rotating.patch','rrzc_rotating.h')]
            +[f'femu@127.0.0.1:{run.target}/'])
        run.ssh(f'cd /home/femu/rocksdb && git apply --check {run.target}/rotating.patch && '
            f'cp {run.target}/rrzc_rotating.h tools/ && git apply {run.target}/rotating.patch && '
            f'make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench '
            f'> {run.target}/编译.log 2>&1',600)
        unchanged={k:v for k,v in expected.items() if k not in ('db_bench','tools/db_bench_tool.cc')}
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(unchanged),180))==unchanged,
                '独立客户端构建改变了库或模型')
        for name in ('db_bench','tools/db_bench_tool.cc','tools/rrzc_rotating.h'):
            expected[name]=run.ssh('cd /home/femu/rocksdb; sha256sum '+name).split()[0]
        stamp(output/'同库身份.json',expected)
        run.ssh(f'cd /home/femu/rocksdb && ./db_bench --db={run.target}/posix '
            '--benchmarks=fillseq --num=65536 --writes=65536 --value_size=4096 '
            '--compression_type=none --disable_wal=1 --seed=20261023 '
            f'> {run.target}/普通目录装载.log 2>&1 && RRZC_ROTATE_ROUNDS=2 RRZC_ROTATE_PHASE_BATCHES=1 '
            f'./db_bench --db={run.target}/posix --benchmarks=rrzcrotating --use_existing_db=1 '
            '--num=65536 --reads=2048 --writes=2048 --threads=1 --value_size=4096 '
            '--compression_type=none --disable_wal=1 --histogram=1 --seed=20261023 '
            f'> {run.target}/普通目录小测.log 2>&1',180)
        run.record(阶段='定制轮转客户端编译及普通目录小测通过，静态库与模型哈希不变')
    except BaseException as failure:error=failure;run.record(原始错误=repr(failure));raise
    finally:
        try:run.finish(error)
        except Exception as cleanup:
            if error is None:raise
            run.record(收尾另有错误=str(cleanup))
    from dbbench_rotating_suite import api_histograms
    check=(out/args.label/'普通目录小测.log').read_text()
    require(all(v['次数']==2048 for v in api_histograms(check).values()) and len(trace(check))==4,
            '真实普通目录批次/读写耗时未核验')
    stamp(output/'准备完成.json',dict(复用原库=True,父镜像=str(IMAGE_DIR/(LABEL+'-prepare.qcow2')),
        软件身份SHA256=digest(output/'同库身份.json')))


def run_arm(args,password,output,stage,mode):
    # 原热点工作器仅负责资源、归档、正常关机；轮转结果用独立核验器解释。
    from dbbench_rotating_worker import worker
    return worker(args,password,output,stage,mode)


def execute(args,password,output):
    state=dict(监督进程PID=os.getpid(),阶段='准备',已完成=[],运行任务={},最大并行=1,
        RR阈值=1024,GC启动百分比=20,结果目录=str(output));running={}
    def record():
        state['运行任务']={str(k):v for k,v in running.items()}
        state['更新时间UTC']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());stamp(output/'状态.json',state)
    record()
    try:
        prepare(args,password,output);freeze(output)
        state['已完成'].append('prepare');record()
        for stage in ('smoke','full'):
            for mode in ('space','model'):
                preflight();state['阶段']=stage+'-'+mode+'串行轮转负载';record()
                pid=os.fork()
                if pid==0:
                    try:run_arm(args,password,output,stage,mode)
                    except BaseException as error:print('原始错误：',stage,mode,repr(error),flush=True);os._exit(1)
                    os._exit(0)
                running[pid]=stage+'-'+mode;record()
                while True:
                    ended,status=os.waitpid(pid,os.WNOHANG)
                    if ended:
                        running.pop(pid);record();require(os.waitstatus_to_exitcode(status)==0,'单臂失败，停止后续')
                        break
                    time.sleep(5)
                state['已完成'].append(stage+'-'+mode);record()
            arms={m:json.loads((output/(stage+'-hot-'+m)/'独立核验.json').read_text()) for m in ('space','model')}
            for key in ('公共参数SHA256','业务参数SHA256','实际读次数','实际写次数','批次请求摘要'):
                require(arms['space'][key]==arms['model'][key],'配对真实请求不一致：'+key)
            stamp(output/(stage+'-hot-配对核验.json'),dict(配对验收通过=True,工作负载='hot',模式=arms,
                边界='定制轮转机会负载；两臂实际批次键序列摘要一致，非标准db_bench，不预设正收益'))
            if stage=='full':
                from plot_dbbench_normalized import render
                render(output,baseline_description='同候选及安全检查下最少有效量Greedy；定制轮转，串行单轮')
                state['最近结果']={m:{k:arms[m][k] for k in ('RR次数','ZC次数','总迁移字节','RR独立改选次数')} for m in arms}
            record()
        state['阶段']='轮转配对及真实API延迟核验完成，全部客机已退出';record()
    except BaseException as error:state.update(阶段='轮转试验停止，正在采证收尾',原始错误=repr(error));record();raise
    finally:
        for pid in running:
            if Path('/proc',str(pid)).exists():os.kill(pid,signal.SIGTERM)
        while running:
            for pid in list(running):
                ended,_=os.waitpid(pid,os.WNOHANG)
                if ended:running.pop(pid)
            record()
            if running:time.sleep(5)
        record()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    parser.add_argument('--allow-unlocked',action='store_true');args=parser.parse_args();args.label=LABEL
    require(args.allow_unlocked,'须明确未锁页');budget=preflight();output=IMAGE_DIR/(LABEL+'-results')
    require(not output.exists(),'结果目录已存在，不覆盖或自动重跑')
    expected=json.loads((SOURCE/'同库身份.json').read_text());old=hot_patched(NATIVE.read_text())
    require(hashlib.sha256(old.encode()).hexdigest()==expected['tools/db_bench_tool.cc'],'补丁父源与实测身份不一致')
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('轮转机会对照认证（仅内存）：');output.mkdir();folder=output/'源码';folder.mkdir()
    for name in ('run_dbbench_rotating_host.py','dbbench_rotating_worker.py','dbbench_rotating_source.py',
                 'dbbench_rotating_suite.py','rrzc_rotating.h','test_dbbench_rotating.py'):
        shutil.copyfile(ROOT/'tests'/name,folder/name)
    (folder/'rotating.patch').write_text(patch(old))
    stamp(output/'预注册计划.json',dict(目的='修正长纯写尾段，检验热点转换时联合维护适用机会；不预设模型优于Greedy',
        RR阈值=1024,GC启动百分比=20,SSD字节=8<<30,装载键数=1048576,读次数=READS,写次数=WRITES,
        批次=24,每批读=32768,每批写=24576,每热点批数=4,热点阶段数=6,种子=20261023,
        热点='每阶段读4096键，80%写相邻61440键；另20%覆盖当前读范围之外，包括旧热点；按批次预定切换',
        调度='单读单写批次汇合，不固定每秒速率；不依赖RR/GC决定下一热点，最多一个小批次读写尾差',
        客户端='新增rrzcrotating；旧ReadRandomRate含1毫秒等待，但上轮调用ReadRandom，不把该等待误认为上轮实际路径',
        不变='原静态库、模型、RR机制、GC触发、候选与安全；只改独立db_bench客户端',
        验收='实际批次数/累计数/热点/键序列摘要配对；真实Get/Put耗时均值及P99/P99.9/P99.99；全键及CRC；原日志重算维护',
        资源=budget,并行数=1,父镜像=str(PARENT),父软件SHA256=digest(SOURCE/'同库身份.json'),
        FEMU二进制SHA256=digest(ROOT.parent.parent/'build-femu/qemu-system-x86_64'),
        结论='机制机会导向定制负载，非公开标准或生产代表性结论；保留全部成功和失败结果，不筛选正结果'))
    if args.detach:
        reader,writer=os.pipe();pid=os.fork()
        if pid:
            os.close(writer);require(os.read(reader,1)==b'1','监督分离失败');os.close(reader)
            print(f'轮转对照监督PID={pid}，目录={output}',flush=True);return
        os.close(reader);os.setsid();fd=os.open(output/'run-监督.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0);os.write(writer,b'1');os.close(writer)
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:execute(args,password,output)
    finally:password=None


if __name__=='__main__':main()
