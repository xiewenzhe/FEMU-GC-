#!/usr/bin/env python3
"""真实SST定制db_bench配对，全块选区逐轮归档；单台客机串行并正常收尾。"""
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
from run_all_blocks_followup_host import PARENT,identity,OLD
from run_joint_host import digest_manifest
from run_tail_ablation_fixed_pair import running_parent
from run_dbbench_parallel_host import stamp
from run_ycsb_quick_host import disk_space,profile8
from ycsb_af import digest,require

LABEL='joint-all-blocks-dbbench-v1-20261025'
FILES=('run_all_blocks_dbbench_host.py','build_all_blocks_dbbench_guest.py',
       'run_all_blocks_dbbench_guest.sh','build_all_blocks_guest.py','strict_stats_guest.py','dbbench_space_probe.cc')

def execute(args,password,output):
    run=RrRun(args,password,output,'dbbench',8101,PARENT);run.profile=profile8();error=None
    run.state.update(边界='8GiB定制db_bench：真实RocksDB SST，串行单轮，非公开标准或稳态',GC启用=True)
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        expected=identity()
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(expected),180))==expected,'父软件或全块对象不符')
        upload=[output/'源码'/name for name in (*FILES,'expected.json')]
        run.command(run.scp()+list(map(str,upload))+[f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='只编译db_bench客户端，复用全块模型对象及原静态库')
        run.ssh(f'python3 {run.target}/build_all_blocks_dbbench_guest.py {run.target}',360)
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/客户端身份.json',str(output/'客户端身份.json')])
        run.ssh('cd /home/femu/rocksdb && '+
            f'{run.target}/db_bench --db={run.target}/posix --benchmarks=fillseq,flush '
            '--num=65536 --writes=65536 --value_size=128 --compression_type=none --disable_wal=1 '+
            f'> {run.target}/普通目录装载.log 2>&1 && RRZC_ROTATE_ROUNDS=2 RRZC_ROTATE_PHASE_BATCHES=1 '
            f'{run.target}/db_bench --db={run.target}/posix --use_existing_db=1 --benchmarks=rrzcrotating '
            '--num=65536 --writes=2048 --reads=2048 --value_size=128 --compression_type=none --disable_wal=1 '
            '--threads=1 --histogram=1 --seed=20261023 '+
            f'> {run.target}/普通目录小测.log 2>&1',180)
        # 原Plan默认24批要求操作数整除；小测显式2批，不能偷偷修改正式参数。
        for mode in ('greedy','blocks'):
            disk_space();run.record(阶段=mode+'：重新装载4GiB后运行24批次真实读写',当前策略=mode)
            run.root(f'nohup setsid bash {run.target}/run_all_blocks_dbbench_guest.sh {mode} '
                f'< /dev/null > {run.target}/{mode}.启动.log 2>&1 & echo $! > {run.target}/{mode}.pid')
            start=changed=time.monotonic();previous=None
            while time.monotonic()-start<1800:
                disk_space()
                point=run.ssh(f'if test -f {run.target}/{mode}/finished; then cat {run.target}/{mode}/wrapper.exit; '
                    f'else echo 运行中; fi; test ! -f {run.target}/{mode}/phase.txt || cat {run.target}/{mode}/phase.txt; '
                    f'test ! -f {run.target}/{mode}/run.log || tail -1 {run.target}/{mode}/run.log; '
                    f'test ! -f {run.target}/{mode}/load.log || stat -c %s {run.target}/{mode}/load.log; '
                    f'test ! -f {run.target}/{mode}/verify.log || stat -c %s {run.target}/{mode}/verify.log; true')
                run.record(最近观测=point)
                print(mode+'：'+point.strip().replace('\n','；'),flush=True)
                if point!=previous:previous=point;changed=time.monotonic()
                require(time.monotonic()-changed<180,'180秒无可见业务进展，采证后正常关机')
                if point.splitlines()[0]!='运行中':require(point.splitlines()[0]=='0',mode+'失败，保存原始证据');break
                time.sleep(10)
            else:raise RuntimeError(mode+'超过30分钟上限，归档关机')
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(expected),180))==expected,
            '运行后原软件身份改变')
        run.record(阶段='两臂完成，归档配置、原始日志和链接证据后正常关机')
    except BaseException as failure:error=failure;run.record(原始错误=repr(failure));raise
    finally:
        try:run.finish(error)
        except Exception as cleanup:
            if error is None:raise
            run.record(收尾另有错误=repr(cleanup))
    run.record(阶段='客机已退出，独立核验真实操作、性能及逐轮选区')
    subprocess.run([sys.executable,str(output/'源码/analyze_all_blocks_dbbench.py'),
        str(output/args.label),str(output/'分析')],check=True)
    run.record(阶段='db_bench配对及逐轮改选核验完成，客机已退出',已完成=['greedy','blocks'])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');p.add_argument('--label',default=LABEL);a=p.parse_args()
    import re
    require(re.fullmatch(r'joint-[a-z0-9-]+',a.label) is not None,'非法标签')
    budget=preflight();require(PARENT.is_file() and not running_parent(PARENT),'父镜像缺失或被占用')
    require(digest(ROOT.parents[1]/'build-femu/qemu-system-x86_64')==json.loads((OLD/'预注册计划.json').read_text())['FEMU_SHA256'],
        'FEMU版本不同')
    output=IMAGE_DIR/(a.label+'-results');require(not output.exists(),'不覆盖已有结果')
    if a.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('真实SST配对认证（仅内存）：');output.mkdir();source=output/'源码';source.mkdir()
    for name in FILES:shutil.copyfile(ROOT/'tests'/name,source/name)
    (source/'expected.json').write_text(json.dumps(identity(),ensure_ascii=False,indent=2))
    shutil.copyfile(ROOT/'analyze_all_blocks_dbbench.py',source/'analyze_all_blocks_dbbench.py')
    stamp(output/'预注册计划.json',dict(目的='真实SST生命周期下验证全块模型净维护成本与逐轮改选',资源=budget,
        平台='8GiB/64Zone、128MiB每Zone、严格RR1024、GC20%、未锁页',策略=['共同候选及安全下最少有效量Greedy','全块同窗成本模型'],
        模型='对象与库保持不变；只编译db_bench客户端并优先链接原全块对象',
        负载='既有定制rrzcrotating；1048576键×4096字节装载；786432读、589824写，24批，每4批换热点，不限速，WAL关闭',
        写入='80%相邻非读热点61440键，20%其他非当前读范围；真实RocksDB压缩和文件退役，不指定删除SST',
        停止点='固定相同操作量，业务后flush及等待压缩；两组各自重新装载，不声称相同物理布局或NAND快照',
        指标='Get/Put平均及P99/P99.9/P99.99；阶段总吞吐；ZC/RR/迁移/全部成功Reset；结束空间；每轮全部观测与安全可选Zone、Greedy反事实和实际Zone',
        验收='编译链接、普通目录小测、批次键摘要配对、全键读取与CRC；后验Greedy仅在同轮同候选上重算，不作为跨策略反事实运行结果',
        边界='定制db_bench非原生公开标准、单轮非稳态；CPU与宿主环境共享；不预设改选次数或正收益',
        收尾='单客机串行；成功失败归档哈希核验后正常关机'))
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:execute(a,password,output)
    finally:password=None

if __name__=='__main__':main()
