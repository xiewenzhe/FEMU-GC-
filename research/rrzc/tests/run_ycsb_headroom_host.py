#!/usr/bin/env python3
"""8GiB盘、4GiB原始值装载的独立F配对；只改装载规模，复用已验收工具和小测。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import signal
import socket

import run_ycsb_quick_host as quick
from run_joint_host import digest_manifest
from run_ycsb_af_host import host_resources
from run_tail_ablation_fixed_pair import running_parent
from ycsb_af import digest,require,LABELS

ROOT=quick.ROOT
IMAGE_DIR=quick.IMAGE_DIR
SOURCE=IMAGE_DIR/'joint-ycsb8-f-v2-20261023-results'
PARENT=IMAGE_DIR/'joint-ycsb8-f-v2-20261023-build.qcow2'
LABEL='joint-ycsb8-f4g-v1-20261023'
RECORDS=1048576
OPERATIONS=300000


class HeadroomRun(quick.QuickRun):
    record_count=RECORDS

    def __init__(self,*args):
        super().__init__(*args)
        self.parent_image=PARENT
        self.state.update(总装载键数=RECORDS,正式业务请求数=OPERATIONS,
            已验收小测来源=str(SOURCE),模式顺序=['full-f-space','full-f-model'],
            边界='8GiB盘，4GiB原始值装载；标准语义F单轮配对，非原6GiB或32GiB试次')

    def build(self):
        # 只建立新的系统盘子镜像；每组内存NAND仍独立重建，不能继承旧NAND。
        self.command(['./build-femu/qemu-img','create','-f','qcow2','-F','qcow2','-b',
            str(PARENT),str(IMAGE_DIR/(self.label_prefix+'-build.qcow2'))])
        self.record(阶段='复用已验收模型、客户端及64Zone探针；即将真实重建NAND装载')


def preflight(args):
    require(args.allow_unlocked,'须明确本轮未锁页')
    require(args.label==LABEL,'本次固定独立标签，拒绝未登记变体')
    output=IMAGE_DIR/(args.label+'-results')
    require(not output.exists() and PARENT.is_file() and not running_parent(PARENT),'结果已存在或父镜像不可用')
    require(os.access('/dev/kvm',os.R_OK|os.W_OK),'KVM不可用')
    for name in ('build','smoke-f-space','smoke-f-model','full-f-space'):
        status=json.loads((SOURCE/name/'阶段归档.json').read_text())
        require(status['完整'] and not status['客机仍存活'],'来源阶段未完整归档退出：'+name)
    old_state=json.loads((SOURCE/'状态.json').read_text())
    require(old_state['当前客机PID'] is None,'来源客机未退出')
    analyzer=quick.module_from(SOURCE/'源码/analyze_ycsb_quick.py','old_quick_analyzer')
    smoke=analyzer.compare_pair(SOURCE,'smoke','f',8192,8192)
    require(smoke['配对验收通过'],'原小测不能复用')
    for name in ('build','full-f-space','full-f-model'):
        tag=args.label+'-'+name
        for path in (IMAGE_DIR/(tag+'.qcow2'),Path('/tmp')/(tag+'.pid'),Path('/tmp')/(tag+'.qmp'),Path('/tmp')/(tag+'-ssh.sock')):
            require(not path.exists(),'新目标已存在：'+str(path))
    with socket.socket() as s:require(s.connect_ex(('127.0.0.1',8096))!=0,'8096被占用')
    binary=digest(quick.PROJECT/'build-femu/qemu-system-x86_64')
    require(binary==json.loads((SOURCE/'预注册计划.json').read_text())['FEMU二进制SHA256'],'设备程序身份漂移')
    p=quick.profile8()
    require(p==json.loads((SOURCE/'平台配置.json').read_text()),'平台改变，不可复用小测')
    return output,p,binary,dict(宿主=host_resources(p),磁盘=quick.disk_space(initial=True))


def freeze(output,p):
    quick.freeze(output,p)
    frozen=output/'源码'
    expected=json.loads((SOURCE/'同库身份.json').read_text())
    target='/home/femu/'+quick.SOURCE.name.removesuffix('-results')
    require(expected[target+'/run_ycsb_quick_guest.sh']==digest(frozen/'run_ycsb_quick_guest.sh'),
            '业务包装器改变，不只装载规模')
    quick.manifest(output,expected)
    plot=(frozen/'plot_ycsb_quick.py').read_text()
    plot=quick.replace_once(plot,'1572864键/30万请求','1048576键（原始值4GiB）/30万请求')
    (frozen/'plot_ycsb_quick.py').write_text(plot)
    for source in (Path(__file__),ROOT/'tests/test_ycsb_headroom.py',ROOT/'tests/dedup_ycsb_archived_tools.py'):
        shutil.copyfile(source,frozen/source.name)
    (output/'复用小测.json').write_text(json.dumps(dict(来源=str(SOURCE),
        配对SHA256=digest(SOURCE/'smoke-f-配对核验.json'),
        说明='同库同设备同包装器已验收小测；正式新装载规模需重新运行并校验，不将小测称为正式结果'),ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default=LABEL)
    parser.add_argument('--allow-unlocked',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--detach',action='store_true')
    args=parser.parse_args()
    output,p,binary,budget=preflight(args)
    if args.check:
        print(json.dumps(dict(预检通过=True,资源=budget,装载键数=RECORDS,原始值字节=RECORDS*4096,业务请求=OPERATIONS),ensure_ascii=False,indent=2));return
    password=getpass.getpass('4GiB装载F配对认证（仅内存）：')
    output.mkdir()
    freeze(output,p)
    (output/'预注册计划.json').write_text(json.dumps(dict(
        目的='修正已确认6GiB装载的压实空间不足；保持8GiB模拟SSD和原模型',
        FEMU二进制SHA256=binary,初始资源=budget,父镜像=str(PARENT),
        唯一业务配置变化='两组recordcount从1572864降到1048576（原始值6GiB降到4GiB），operationcount仍300000',
        不变='8GiB/64个128MiB Zone/每区128Block、RR1024、GC20%、模型/候选/预留分配机制/业务二进制/线程/种子/不限速/无WAL缓存',
        验收='复用已验收同设备小测；两组新建NAND真实装载，全键/请求/维护/评分/归档/关机均通过才出图；无收益亦保留',
        磁盘='保留启动36GiB data预算及双盘运行中保护；闭合工具副本同哈希硬链接，日志与路径不删',
        边界='装载4GiB仅是留出更多临时空间的测试配置，不是已证明安全；单轮非稳态，API非裸IO，擦除由Reset乘128推导'),ensure_ascii=False,indent=2))
    if args.detach:
        r,w=os.pipe();pid=os.fork()
        if pid:
            os.close(w);require(os.read(r,1)==b'1','监督分离失败');os.close(r)
            print(f'4GiB装载F监督PID={pid}，状态={output/"状态.json"}',flush=True);return
        os.close(r);os.setsid()
        fd=os.open(output/'监督.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
        os.write(w,b'1');os.close(w)
    run=HeadroomRun(args,password,output,p);run.qemu_digest=binary
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        analyzer=quick.module_from(output/'源码/analyze_ycsb_quick.py','headroom_analyzer')
        run.build()
        for mode in LABELS:run.arm('full',mode,analyzer)
        pair=analyzer.compare_pair(output,'full','f',RECORDS,OPERATIONS)
        (output/'full-f-配对核验.json').write_text(json.dumps(pair,ensure_ascii=False,indent=2))
        quick.module_from(output/'源码/plot_ycsb_quick.py','headroom_plot').render(output)
        run.record(阶段='4GiB装载F配对及图表完成，全部客机关闭',最近差值=pair['差值'],图表目录=str(output/'图表'))
    except BaseException as error:
        run.record(阶段='4GiB装载F停止，保留错误，不自动重跑',错误=type(error).__name__+'：'+str(error));raise
    finally:run.password=None


if __name__=='__main__':main()
