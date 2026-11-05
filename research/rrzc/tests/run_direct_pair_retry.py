#!/usr/bin/env python3
"""保留首次装载前失败，仅修正调度器初始化顺序后用新镜像执行同一配对。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import socket
from types import SimpleNamespace
import run_direct_host as host
from run_stream_pair import require
from run_tail_ablation_fixed_pair import running_parent

PREFIX='direct-ycsb-r2-20261020'
BASE=host.OUTPUT
OUTPUT=host.IMAGE_DIR/(PREFIX+'-results')


def preflight():
    require(not OUTPUT.exists() and (BASE/'同库身份.json').is_file(),'新输出已存在或构建身份缺失')
    require(json.loads((BASE/'smoke/独立核验.json').read_text())['验收通过'],'统一模型设备短测未通过')
    require(json.loads((BASE/'状态.json').read_text()).get('当前客机PID') is None and
        not running_parent(host.BUILD),'旧任务未关闭')
    with socket.socket() as s:require(s.connect_ex(('127.0.0.1',8096))!=0,'端口被占用')
    for mode in ('greedy','direct'):
        for p in (host.IMAGE_DIR/f'{PREFIX}-{mode}.qcow2',Path(f'/tmp/{PREFIX}-{mode}.pid'),
            Path(f'/tmp/{PREFIX}-{mode}.qmp'),Path(f'/tmp/{PREFIX}-{mode}-ssh.sock')):
            require(not p.exists(),'新隔离目标已存在')
    host.resources()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');p.add_argument('--detach',action='store_true')
    args=p.parse_args();preflight()
    if args.check:print('旧客机已关闭、构建及功能通过，新隔离路径和资源检查通过');return
    password=getpass.getpass('隔离客机认证（仅内存）：')
    OUTPUT.mkdir();(OUTPUT/'smoke').mkdir()
    shutil.copyfile(BASE/'同库身份.json',OUTPUT/'同库身份.json')
    shutil.copyfile(BASE/'smoke/独立核验.json',OUTPUT/'smoke/独立核验.json')
    shutil.copyfile(Path(__file__),OUTPUT/'本轮重试入口.py')
    (OUTPUT/'来源.json').write_text(json.dumps(dict(实际编译及短测归档=str(BASE),
        旧配对='因探测前未设置调度器，业务装载前停止；原失败未覆盖',
        唯一修正='调度器设置移到第一次只读设备代际探测之前；不改变模型、库、负载、统计或阈值'),ensure_ascii=False,indent=2))
    if args.detach:
        r,w=os.pipe();pid=os.fork()
        if pid:
            os.close(w);require(os.read(r,1)==b'1','监督未就绪');os.close(r)
            print(f'监督PID={pid}，状态={OUTPUT / "状态.json"}',flush=True);return
        os.close(r);os.setsid();fd=os.open(OUTPUT/'监督.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0);os.write(w,b'1');os.close(w)
    # 目标客机标签改变；编译目录和归档目录名仍指向已验证的同一库。
    host.OUTPUT=OUTPUT;host.DirectRun.label_prefix=PREFIX
    from run_direct_pair import run_pair
    run=host.DirectRun(SimpleNamespace(label=PREFIX,seconds=1200,space_weight=50),password,OUTPUT)
    try:run_pair(run)
    except BaseException as e:
        run.record(阶段='本轮停止，未自动重跑',错误=str(e) if isinstance(e,(ValueError,RuntimeError)) else type(e).__name__)
        raise


if __name__=='__main__':main()
