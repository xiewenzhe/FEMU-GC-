#!/usr/bin/env python3
"""只读采证长Flush区间的客机线程阻塞；有期限、样本上限和严格PID身份检查。"""
import argparse
import json
import os
from pathlib import Path
import re
import time


def run(root,limit):
    assert os.geteuid()==0 and root.parent==Path('/home/femu') and root.name.startswith('joint-controlled-')
    assert root.is_dir() and not root.is_symlink()
    dest=root/'Flush阻塞采证.jsonl';started=time.monotonic();counts={};last={};positions={};pending={}
    with dest.open('x') as output:
        def emit(row):output.write(json.dumps(row,ensure_ascii=False)+'\n');output.flush()
        emit(dict(事件='观察开始',PID=os.getpid(),期限秒=limit,只读=True,采样门槛毫秒=100))
        while time.monotonic()-started<limit:
            for tag in ('g1','g2'):
                stage=root/tag
                if (stage/'finished').exists():continue
                log=stage/'run.log';pidfile=stage/'bench.pid'
                if not log.exists() or not pidfile.exists():continue
                pid=int(pidfile.read_text());proc=Path('/proc')/str(pid)
                try:
                    if (proc/'exe').resolve(strict=True)!=(root/'db_bench').resolve(strict=True):continue
                    offset=positions.get(tag,0)
                    with log.open() as stream:stream.seek(offset);new=stream.read();positions[tag]=stream.tell()
                    # fprintf整行写入；只处理已出现的分段，不推测尚未出现的区间。
                    for job,file,phase,ns in re.findall(r'Flush分段：任务=(\d+)，文件=(\d+)，阶段=([^，]+)，纳秒=(\d+)',new):
                        key=(tag,int(job))
                        pending[key]=(phase,int(ns),int(file))
                    now=time.monotonic_ns()
                    for key,(phase,begin,file) in list(pending.items()):
                        if key[0]!=tag or not phase.endswith('前') or now-begin<100000000:continue
                        if counts.get(key,0)>=8 or now-last.get(key,0)<100000000:continue
                        snapshots=[]
                        for task in (proc/'task').iterdir():
                            values={}
                            for name in ('comm','wchan','syscall','stack','schedstat'):
                                try:values[name]=(task/name).read_text().strip()
                                except (OSError,ProcessLookupError) as exc:values[name]=repr(exc)
                            # 内核栈确认是x86_64 fsync时，补充该调用真实fd的目标，不能只按时点猜路径。
                            if '__x64_sys_fsync' in values.get('stack',''):
                                try:
                                    words=values['syscall'].split();descriptor=int(words[1],0)
                                    values['同步文件描述符']=descriptor
                                    values['同步描述符目标']=os.readlink(proc/'fd'/str(descriptor))
                                except (OSError,ValueError,IndexError) as exc:
                                    values['同步描述符采证错误']=repr(exc)
                            snapshots.append(dict(TID=int(task.name),内容=values))
                        emit(dict(事件='长区间线程快照',组=tag,任务=key[1],文件=file,阶段=phase,
                            起点纳秒=begin,采证纳秒=now,已等待秒=(now-begin)/1e9,业务PID=pid,线程=snapshots,
                            磁盘统计=Path('/proc/diskstats').read_text(),内存统计=Path('/proc/meminfo').read_text()))
                        counts[key]=counts.get(key,0)+1;last[key]=now
                except (FileNotFoundError,ProcessLookupError):continue
            if (root/'g2/finished').exists():break
            time.sleep(.05)
        emit(dict(事件='观察结束',耗时秒=time.monotonic()-started,样本数=sum(counts.values()),
                  第二轮已结束=(root/'g2/finished').exists()))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    parser.add_argument('--seconds',type=int,default=1000);args=parser.parse_args()
    assert 1<=args.seconds<=1200
    run(args.root,args.seconds)
