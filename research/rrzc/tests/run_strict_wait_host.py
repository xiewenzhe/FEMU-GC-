#!/usr/bin/env python3
"""隔离修复严格RR覆盖等待，真实回归通过后重跑同一轮转配对。"""
import argparse
import getpass
import json
import os
import shutil
import signal
import time
from pathlib import Path
from run_dbbench_parallel_host import DbRun,ROOT,IMAGE_DIR,stamp
from run_joint_host import digest_manifest
from ycsb_af import require,digest
from strict_wait_patch import make_patch
import run_dbbench_rotating_host as rotating

LABEL='joint-dbbench8-rotating-v9-20261024'
SOURCE=IMAGE_DIR/'joint-dbbench8-rotating-v2-20261024-results'
PARENT=IMAGE_DIR/'joint-dbbench8-rotating-v4-20261024-prepare.qcow2'
INSPECT=IMAGE_DIR/'joint-rr-coverage-inspect-v1-20261024-results/源码'


def prepare(args,password,output):
    out=output/'prepare';out.mkdir();run=DbRun(args,password,out,'prepare',8101,PARENT);error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        expected=json.loads((SOURCE/'同库身份.json').read_text())
        changed={'db_bench','librocksdb_debug.a','plugin/zenfs/fs/fs_zenfs.cc','plugin/zenfs/fs/io_zenfs.cc',
            'plugin/zenfs/fs/zbd_zenfs.h'}
        unchanged={k:v for k,v in expected.items() if k not in changed}
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(unchanged),180))==unchanged,
            '父镜像软件漂移')
        from strict_wait_patch import patched
        import hashlib
        for name in ('io_zenfs.cc','fs_zenfs.cc','zbd_zenfs.h'):
            predicted=hashlib.sha256(patched(name,(INSPECT/name).read_text()).encode()).hexdigest()
            require(run.ssh('sha256sum /home/femu/rocksdb/plugin/zenfs/fs/'+name).split()[0]==predicted,
                '复用的已编译修复源不符：'+name)
        require(run.ssh('sha256sum /home/femu/rocksdb/plugin/zenfs/fs/rrzc_strict_wait.h').split()[0]==
            digest(ROOT/'model/rrzc_strict_wait.h'),'已编译等待头不符')
        run.command(run.scp()+[str(output/'源码'/n) for n in ('strict-wait.patch','rrzc_strict_wait.h',
            'strict_wait_device.cc','strict_stats_guest.py')]+[f'femu@127.0.0.1:{run.target}/'])
        run.ssh(f'cd /home/femu/rocksdb/plugin/zenfs && git apply --reverse --check {run.target}/strict-wait.patch && '
            'cd /home/femu/rocksdb && '
            'g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude '
            f'{run.target}/strict_wait_device.cc librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt '
            f'-o {run.target}/strict_wait_device > {run.target}/回归编译.log 2>&1',900)
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb; sha256sum '+' '.join(unchanged),180))==unchanged,
            '修复意外改变模型、阈值、客户端源或其他接线')
        for name in sorted(changed|{'plugin/zenfs/fs/rrzc_strict_wait.h'}):
            expected[name]=run.ssh('cd /home/femu/rocksdb; sha256sum '+name).split()[0]
        stamp(output/'同库身份.json',expected)
        # 新客机NAND须全空；格式化仅针对本次新建的独立模拟设备。
        run.root(f'test "$(blockdev --getsize64 /dev/nvme0n1)" = 8589934592 && '
            "printf 'mq-deadline\\n' > /sys/block/nvme0n1/queue/scheduler && "
            f'mkdir {run.target}/aux && cd /home/femu/rocksdb && '
            f'plugin/zenfs/util/zenfs mkfs --zbd=nvme0n1 --aux_path={run.target}/aux --enable_gc '
            f'> {run.target}/回归格式化.log 2>&1',60)
        env='env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 '
        env+=' '.join(n+'=1' for n in ('ZENFS_RR_STRICT','ZENFS_RR_STRICT_AUDIT','ZENFS_RR_ENABLE',
            'ZENFS_MAINT_DIAG','ZENFS_ALLOC_DIAG','ZENFS_ALLOC_FALLBACK','ZENFS_MIGRATE_RESERVE',
            'ZENFS_RRZC_CONTENT_GUARD','ZENFS_RRZC_MIGRATION_SPARE','ZENFS_RRZC_EMPTY_RESERVE',
            'ZENFS_RRZC_RESERVE_TARGET','ZENFS_RRZC_SINGLE_RECOVERY','ZENFS_RR_FAIR_QUEUE'))
        run.root(f'{env} {run.target}/strict_wait_device {run.target}/覆盖回归.zenfs.log '
            f'> {run.target}/覆盖回归.log 2>&1',90)
        run.root(f'python3 {run.target}/strict_stats_guest.py > {run.target}/回归严格计数.json')
        stats=json.loads(run.ssh('cat '+run.target+'/回归严格计数.json'))
        require(stats['历史最高RC']==1024 and stats['阈值触发事件']>=1 and stats['自身刷新豁免读取']>0,
            '设备严格计数未验证到真实阈值及自身RR读豁免')
        log=run.ssh('cat '+run.target+'/覆盖回归.log')
        require('真实严格RR覆盖回归通过' in log,'真实回归没有完成')
        evidence=run.ssh(f"python3 -c 'from pathlib import Path;print(\"\".join(s for s in Path(\"{run.target}/覆盖回归.zenfs.log\").open() if \"RR 延后\" in s or \"RR 完成\" in s))'")
        require('RR 延后' in evidence and 'RR 完成' in evidence,'未真实复现覆盖等待再完成RR')
        stamp(output/'覆盖修复验收.json',dict(通过=True,设备计数=stats,回归输出=log,覆盖与维护证据=evidence,
            不变='同一模型、RR1024、GC20%、候选与完整覆盖检查；仅调整严格读等待锁范围'))
        run.record(阶段='库编译及真实开放SST同区覆盖回归通过，准备完整配对')
    except BaseException as failure:error=failure;run.record(原始错误=repr(failure));raise
    finally:
        try:run.finish(error)
        except Exception as cleanup:
            if error is None:raise
            run.record(收尾另有错误=str(cleanup))
    stamp(output/'准备完成.json',dict(父镜像=str(IMAGE_DIR/(LABEL+'-prepare.qcow2')),
        软件身份SHA256=digest(output/'同库身份.json'),真实覆盖回归=True))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args();args.label=LABEL
    rotating.LABEL=LABEL;rotating.PARENT=PARENT;rotating.SOURCE=SOURCE;rotating.prepare=prepare
    output=IMAGE_DIR/(LABEL+'-results')
    from relocate_coverage_build import relocate
    if args.resume:
        require(json.loads((output/'覆盖修复验收.json').read_text())['通过'],'没有真实回归验收，不能续跑')
        require((output/'准备完成.json').is_file(),'缺少准备身份')
        import hashlib
        from dbbench_rotating_suite import wrapper_source,capture_source,analysis_source
        for name,text in (('run_dbbench_guest.sh',wrapper_source()),('capture_dbbench_guest.py',capture_source()),
            ('analyze_dbbench_rotating.py',analysis_source())):
            require(digest(output/'源码'/name)==hashlib.sha256(text.encode()).hexdigest(),'续跑业务/核验源漂移：'+name)
        # 每阶段已归档关机才移动镜像；保留原路径链接，不改变NAND起始语义。
        for tag in ('prepare','smoke-hot-space','smoke-hot-model','full-hot-space','full-hot-model'):
            if (output/tag/'阶段归档.json').is_file():relocate(LABEL,tag)
        def reuse_prepare(*_):pass
        rotating.prepare=reuse_prepare
        rotating.freeze=lambda _:None
        old_worker=rotating.run_arm
        def resume_arm(args,password,output,stage,mode):
            tag=stage+'-hot-'+mode;done=output/tag/'独立核验.json'
            if done.is_file():
                status=json.loads((output/tag/'阶段归档.json').read_text())
                require(status['完整'] and not status['客机仍存活'] and status['业务成功'],'已完成臂退出未核验')
                return
            old_worker(args,password,output,stage,mode)
            relocate(LABEL,tag)
        rotating.run_arm=resume_arm
    else:require(not output.exists(),'不覆盖已有结果')
    budget=rotating.preflight()
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('严格RR修复与重跑认证（仅内存）：')
    revision=str(time.time_ns())
    if args.resume:
        # 已验收业务源和结果保持原样，续跑入口单独存档。
        shutil.copyfile(__file__,output/('源码/run_strict_wait_host-续跑-'+revision+'.py'))
        shutil.copyfile(ROOT/'tests/relocate_coverage_build.py',output/('源码/relocate_coverage_build-'+revision+'.py'))
        old=json.loads((output/'状态.json').read_text())
        stamp(output/('续跑前状态-'+revision+'.json'),old)
        stamp(output/('续跑资源-'+revision+'.json'),budget)
    else:output.mkdir();(output/'源码').mkdir()
    folder=output/'源码'
    if not args.resume:initialize(output,folder,budget)
    if args.detach:
        reader,writer=os.pipe();pid=os.fork()
        if pid:
            os.close(writer);require(os.read(reader,1)==b'1','监督分离失败');os.close(reader)
            print(f'覆盖修复及重跑监督PID={pid}，目录={output}',flush=True);return
        os.close(reader);os.setsid();name='run-续跑监督-'+revision+'.log' if args.resume else 'run-监督.log'
        fd=os.open(output/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0);os.write(writer,b'1');os.close(writer)
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:rotating.execute(args,password,output)
    finally:password=None


def initialize(output,folder,budget):
    for source in (ROOT/'tests/run_strict_wait_host.py',ROOT/'tests/strict_wait_patch.py',ROOT/'tests/strict_wait_device.cc',
        ROOT/'tests/strict_stats_guest.py',ROOT/'model/rrzc_strict_wait.h',ROOT/'tests/test_strict_wait.cc',ROOT/'tests/test_strict_wait.py'):
        shutil.copyfile(source,folder/source.name)
    (folder/'strict-wait.patch').write_text(make_patch(INSPECT))
    stamp(output/'预注册计划.json',dict(目的='修复开放SST覆盖等待与全局严格读锁冲突后重跑完全相同轮转负载',
        父镜像=str(PARENT),父软件SHA256=digest(SOURCE/'同库身份.json'),RR阈值=1024,GC启动百分比=20,
        请求配置来源=str(SOURCE/'预注册计划.json'),读次数=786432,写次数=589824,批次=24,
        不变='FEMU、模型、候选、阈值、GC条件、键序列；两臂均使用相同修复库',
        修复='到期当前请求暂停；单轮维护未完成返回独立完成标记，在全局读锁外等待，设备继续阻止到期块普通读',
        退出='等待30秒仍不安全则明确失败；归档正常关机；不扩大阈值或移除覆盖保护',
        验证='并发纯核、补丁可逆、库编译、真实开放SST覆盖回归、两臂小测、完整配对与全键CRC',资源=budget))


if __name__=='__main__':main()
