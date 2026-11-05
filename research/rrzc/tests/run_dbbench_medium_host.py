#!/usr/bin/env python3
"""五倍热点中测：同库Greedy/模型并行，两轮依次执行，原始试次不覆盖。"""
import argparse
import csv
import getpass
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
from types import SimpleNamespace

import dbbench_hotspot_suite as hotspot
from dbbench_medium_stats import capture_source,operation_count,segment_rows
from run_dbbench_parallel_host import ROOT,IMAGE_DIR,capacity,stamp
from run_tail_ablation_fixed_pair import running_parent
from ycsb_af import digest,require

LABEL='joint-dbbench8-locality-medium-v1-20261024'
SOURCE=IMAGE_DIR/'joint-dbbench8-locality-v4-20261024-results'
PARENT=IMAGE_DIR/'joint-dbbench8-locality-v4-20261024-prepare.qcow2'
READS=2000000
WRITES=3000000


def preflight():
    require(PARENT.is_file() and not running_parent(PARENT),'软件父镜像缺失或正被使用')
    require(json.loads((SOURCE/'状态.json').read_text())['运行任务']=={},'先导仍有登记任务')
    listing=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
    require(str(IMAGE_DIR)+'/' not in listing,'本项目已有客机，不叠加')
    for port in (8101,8102):
        with socket.socket() as sock:require(sock.connect_ex(('127.0.0.1',port))!=0,'实验端口占用')
    budget=capacity();require(budget['资源允许并发']>=2,'不足两台原预算，不降低保护门槛')
    return budget


def freeze_repeat(output):
    output.mkdir();(output/'源码').mkdir()
    shutil.copyfile(SOURCE/'同库身份.json',output/'同库身份.json')
    hotspot.freeze(output,writes=WRITES,capture_source=capture_source())
    for name in ('run_dbbench_medium_host.py','dbbench_medium_stats.py','test_dbbench_medium.py',
                 'run_dbbench_parallel_host.py','dbbench_suite.py'):
        shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    stamp(output/'准备完成.json',dict(复用父镜像=str(PARENT),
        软件身份SHA256=digest(SOURCE/'同库身份.json'),
        说明='只复用已编译软件；每台全新内存NAND，不继承先导运行状态；未重新编译库或模型'))


def finish_pair(output):
    arms={m:json.loads((output/('full-hot-'+m)/'独立核验.json').read_text()) for m in ('space','model')}
    for key in ('公共参数SHA256','业务参数SHA256','实际读次数','实际写次数'):
        require(arms['space'][key]==arms['model'][key],'两臂参数或请求量不一致：'+key)
    for mode,data in arms.items():
        out=output/('full-hot-'+mode)
        archive=json.loads((out/'阶段归档.json').read_text())
        require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活'],'归档或退出未通过')
        points=[json.loads(s) for s in (out/'操作进度采样.jsonl').read_text().splitlines()]
        require(operation_count(points[-1]['观测'])==READS+WRITES,'末次采样没有确认全部业务操作')
        phases=points[-1]['观测']['分阶段维护']
        for key in ('RR次数','ZC次数','RR迁移字节','ZC迁移字节'):
            require(sum(phases.get(p,{}).get(key,0) for p in ('load','run'))==data[key],
                    '增量分段统计与完整原始日志不一致：'+key)
        require(sum(phases.get(p,{}).get('成功Reset',0) for p in ('load','run'))==data['总成功ZoneReset'],
                '增量Reset与完整日志不一致')
        rows=segment_rows(points,READS+WRITES)
        require(rows and rows[-1]['观测累计操作下界']==READS+WRITES,'缺少业务终点分段')
        with (out/'按操作进度维护.csv').open('x',newline='',encoding='utf-8-sig') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        stamp(out/'分段核验.json',dict(验收通过=True,分阶段累计=phases,分段=rows,
            边界='只读顺序采样，操作报告为下界，百万切点允许越界且不插值；阶段run不含装载及全键校验'))
    pair=dict(配对验收通过=True,工作负载='hot',阶段='full',模式=arms,
        边界='同种子及请求数；读写交错和压实布局不保证相同；并行中测，不预设稳态或机制因果收益')
    stamp(output/'full-hot-配对核验.json',pair)
    from plot_dbbench_normalized import render
    render(output,baseline_description='同候选及安全检查下最少有效量Greedy；五倍热点中测，非stock批量GC')
    return pair


def execute(password,output,budget):
    state=dict(监督进程PID=os.getpid(),阶段='五倍中测准备',已完成轮次=[],运行任务={},
        RR阈值=1024,GC启动百分比=20,每臂读次数=READS,每臂写次数=WRITES,最大并行=2,
        重复次数=2,结果目录=str(output))
    running={}
    def record():
        state['运行任务']={str(pid):tag for pid,tag in running.items()}
        state['更新时间UTC']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
        stamp(output/'状态.json',state)
    record()
    try:
        for repeat in (1,2):
            budget=preflight();sets=hotspot.cpu_sets(budget['逻辑CPU'],2)
            # 第二轮交换两臂CPU集合，避免固定一组核心造成偏置；请求种子不变。
            if repeat==2:sets.reverse()
            label=LABEL+'-r'+str(repeat);part=output/(label+'-results')
            freeze_repeat(part)
            state.update(阶段='第'+str(repeat)+'轮Greedy/模型并行中测',当前轮次=repeat,
                当前轮目录=str(part),CPU集合=sets,资源预算=budget)
            record()
            for slot,mode in enumerate(('space','model')):
                pid=os.fork()
                if pid==0:
                    try:
                        hotspot.worker(SimpleNamespace(label=label,allow_unlocked=True),password,part,
                            'full',mode,slot,sets[slot],parent_image=PARENT,read_count=READS,checkpoints=True)
                    except BaseException as error:
                        print('原始错误：',repeat,mode,repr(error),flush=True);os._exit(1)
                    os._exit(0)
                running[pid]=label+'/'+mode;record()
            while running:
                for pid in list(running):
                    ended,status=os.waitpid(pid,os.WNOHANG)
                    if ended:
                        tag=running.pop(pid);record()
                        require(os.waitstatus_to_exitcode(status)==0,'单臂失败，停止后续并收尾：'+tag)
                if running:time.sleep(5)
            pair=finish_pair(part)
            state['已完成轮次'].append(repeat)
            state['最近结果']={mode:{k:pair['模式'][mode][k] for k in
                ('RR次数','ZC次数','总迁移字节','RR独立改选次数','业务进程吞吐ops每秒')} for mode in ('space','model')}
            record()
        state['阶段']='两轮五倍中测及分段核验完成，客机全部退出';record()
        stamp(output/'两轮结果.json',dict(验收通过=True,各轮=[
            json.loads((output/(LABEL+'-r'+str(n)+'-results')/'full-hot-配对核验.json').read_text()) for n in (1,2)],
            限制='保留各轮原值与比值；不合并分位数，不将两个样本视为统计显著或自动证明稳态'))
    except BaseException as error:
        state.update(阶段='中测失败，正在正常收尾',原始错误=repr(error));record();raise
    finally:
        for pid in running:
            if Path('/proc',str(pid)).exists():
                require(Path('/proc',str(pid)).stat().st_uid==os.getuid(),'工作进程归属变化')
                os.kill(pid,signal.SIGTERM)
        while running:
            for pid in list(running):
                ended,_=os.waitpid(pid,os.WNOHANG)
                if ended:running.pop(pid)
            record()
            if running:time.sleep(5)
        if state.get('原始错误'):state['阶段']='中测停止，工作进程已回收；详见各臂归档及关机状态'
        record()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--allow-unlocked',action='store_true')
    parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();require(args.allow_unlocked,'必须明确使用用户已授权的未锁页模式')
    budget=preflight();output=IMAGE_DIR/(LABEL+'-results')
    require(not output.exists(),'独立结果目录已存在，不覆盖或自动重跑')
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('五倍热点中测认证（仅内存）：')
    output.mkdir()
    stamp(output/'预注册计划.json',dict(目的='检验一次RR改选是否转化为持续总维护成本收益，不预设正结果',
        RR阈值=1024,GC启动百分比=20,SSD字节=8<<30,装载键数=1048576,
        每臂读次数=READS,每臂写次数=WRITES,重复轮次=2,种子=20261023,限速=False,
        负载='沿用相邻稳定读热点与覆盖写，仅业务读写数同比五倍，非原生标准db_bench',
        复用父镜像=str(PARENT),软件身份SHA256=digest(SOURCE/'同库身份.json'),
        FEMU二进制SHA256=digest(ROOT.parent.parent/'build-femu/qemu-system-x86_64'),
        资源=budget,策略='同候选最少有效量Greedy与原去边界联合成本模型，不改库或RR/GC',
        分段='每10秒只读轮询，每百万累计操作记录首个达到的观测；读写完成数和越界量保留，不插值',
        保护='每轮两台，上一轮归档正常关机后才下一轮；保留原磁盘内存门槛，失败停止排队并采证关机'))
    if args.detach:
        reader,writer=os.pipe();pid=os.fork()
        if pid:
            os.close(writer);require(os.read(reader,1)==b'1','监督分离失败');os.close(reader)
            print(f'五倍热点中测监督PID={pid}，结果目录={output}',flush=True);return
        os.close(reader);os.setsid()
        fd=os.open(output/'run-监督.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
        os.write(writer,b'1');os.close(writer)
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:execute(password,output,budget)
    finally:password=None


if __name__=='__main__':main()
