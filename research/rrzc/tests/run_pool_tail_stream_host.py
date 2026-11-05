"""同一进程24窗口持续对照；关闭组先重复，保留在线H历史和原维护规则。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import shlex
import subprocess
import sys

from run_pool_tail_goal_host import prepare as frozen_prepare,PRIOR
from run_integrated_greedy_host import (BASELINE,BINARY,CalibrationRun,PARENT,ROOT,IMAGE_DIR,
    admission,check_baseline_budget,detach_supervisor,preflight,profile8,stamp,wait_stage,
    stop_business,preserve_unverified_image,digest,require,collect,repeat_guard)
from sampling_scope_bundle import closed
from result_storage import configure as configure_storage,compact as compact_storage,check_observation
from pool_tail_stream_source import wrapper,WINDOWS,REQUESTS
from analyze_integrated_pool import arm


def stop_stream(run):
    # 先核对PID身份；SIGTERM请求退出后等待包装器落盘，禁止边写边归档。
    script=r'''from pathlib import Path
import os,signal,time
root=Path(__import__('sys').argv[1])
for marker in root.glob('*/bench.pid'):
    if (marker.parent/'finished').exists():continue
    pid=int(marker.read_text());proc=Path('/proc')/str(pid)
    if not proc.exists():continue
    try:actual=(proc/'exe').resolve(strict=True)
    except FileNotFoundError:continue
    if actual!=root/'db_bench':raise RuntimeError('业务PID身份不符，不发送退出信号')
    os.kill(pid,signal.SIGTERM)
    limit=time.monotonic()+30
    while proc.exists() and time.monotonic()<limit:time.sleep(.1)
    if proc.exists():raise RuntimeError('退出请求后仍存活，保留证据并正常关机')
    while not (marker.parent/'finished').exists() and time.monotonic()<limit:time.sleep(.1)
    if not (marker.parent/'finished').exists():raise RuntimeError('包装器尚未退出，不声称可稳定归档')
'''
    run.root('python3 -c '+shlex.quote(script)+' '+shlex.quote(run.target),45)
    stop_business(run)


def prepare(output):
    scorer,target=frozen_prepare(output);source=output/'源码';old=closed(PRIOR)
    for n in ('build_pool_tail_stream_guest.py','pool_tail_stream_source.py','build_all_blocks_dbbench_guest.py','calibration_source.py'):
        shutil.copyfile(ROOT/'tests'/n,source/n)
    shutil.copyfile(ROOT/'model/rrzc_stream_phase.h',source/'rrzc_stream_phase.h')
    for p in old.glob('rrzc*.h'):shutil.copyfile(p,source/p.name)
    # 旧入口只复用程序，重编客户端必须同时带齐原根目录内的实验头文件。
    for p in [old/'校准-db_bench_tool.cc',*source.glob('rrzc*.h')]:
        for name in re.findall(r'^#include "(rrzc[^"/]+\.h)"',p.read_text(),re.M):
            require((source/name).is_file() or (old/'协同池副本/plugin/zenfs/fs'/name).is_file(),
                '传输缺少客户端头文件：'+name)
    supports=('诊断-flush_job.cc.o','诊断-sync_point.cc.o','诊断-sync_point_impl.cc.o')
    for n in supports:shutil.copyfile(old/n,source/n)
    stamp(source/'持续负载支持对象.json',{n:digest(source/n) for n in supports})
    text=wrapper((PRIOR/'源码/run_pool_business_guest.sh').read_text())
    (source/'run_pool_business_guest.sh').write_text(text)
    subprocess.run(['bash','-n',str(source/'run_pool_business_guest.sh')],check=True)
    # 宿主控制及分析源码也保存身份，不再依赖事后回忆入口版本。
    for n in ('run_pool_tail_stream_host.py',):shutil.copyfile(ROOT/'tests'/n,source/n)
    for n in ('analyze_pool_tail_stream.py','analyze_integrated_pool.py'):shutil.copyfile(ROOT/n,source/n)
    # -I隔离宿主PYTHONPATH，仅允许实际传输目录提供本地构建依赖。
    subprocess.run([sys.executable,'-I','-c',
        'import sys;sys.path.insert(0,sys.argv[1]);import build_pool_tail_stream_guest',str(source)],
        check=True,timeout=30)
    return scorer,target


def execute(args,password,output,scorer,target):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.target=target;run.state.update(标签=Path(target).name,宿主实验标签=args.label,
        客机工作目录=target,GC启用=True,固定K=1,选区='Greedy',持续窗口=WINDOWS,
        边界='每臂连续24窗口，6/12/18/24只读采样，不在业务阶段间重新挂载或Reset')
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    configure_storage(run,input_source=PRIOR,ftl_audit=args.ftl_audit);error=None
    try:
        run.start();run.ssh(f'test ! -e {target} && mkdir {target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+[f'femu@127.0.0.1:{target}/'],180)
        run.record(阶段='恢复共同输入，仅增加日志观测并同ABI编译连续客户端')
        for script in ('restore_calibration_guest.py','reuse_flush_origin_guest.py','restore_pool_input_guest.py','build_pool_tail_stream_guest.py'):
            run.ssh(f'python3 {target}/{script} {target}',240)
        run.command(run.scp()+[f'femu@127.0.0.1:{target}/持续负载构建.json',str(output/'持续负载构建.json')])
        for tag in ('p1','p2','m1','m2'):
            run.record(阶段=tag+'连续24窗口完整业务',尾部复用执行=tag.startswith('m'))
            wait_stage(run,tag,'run_pool_business_guest.sh',3600,mode='greedy')
            path=collect(run,tag);report=arm(path,True,scorer,pool_k=1,windows=WINDOWS)
            stamp(output/(tag+'-核验.json'),report)
            from analyze_pool_tail_stream import phase_arm,prefix_proof
            stamp(output/(tag+'-阶段核验.json'),phase_arm(path,report))
            stamp(output/(tag+'-旧请求前缀核验.json'),prefix_proof(PRIOR/'逐臂采证'/('m1' if tag.startswith('m') else 'p1'),path))
            run.record(已完成=[*run.state.get('已完成',[]),tag])
            if tag in ('p2','m2'):
                first=output/'逐臂采证'/('p1' if tag=='p2' else 'm1')
                proof=repeat_guard(first,path,request_count=REQUESTS)
                a=json.loads((output/(('p1' if tag=='p2' else 'm1')+'-阶段核验.json')).read_text())
                b=json.loads((output/(tag+'-阶段核验.json')).read_text())
                proof['阶段维护空间相同']=a['阶段']==b['阶段'] and a['关闭阶段维护']==b['关闭阶段维护']
                proof['通过']=proof['通过'] and proof['阶段维护空间相同']
                stamp(output/(('关闭' if tag=='p2' else '开启')+'重复核验.json'),proof)
                require(proof['通过'],'新配置阶段或完整账目重复有分歧，停止收益比较')
    except BaseException as failed:
        error=failed;run.record(原始错误=repr(failed))
        if run.deployed:
            try:stop_stream(run)
            except Exception as stopped:run.record(业务中断反馈=repr(stopped))
        raise
    finally:
        archived=False
        try:run.finish(error);archived=True
        except Exception as cleanup:
            retained=preserve_unverified_image(run,cleanup)
            if error is None and not retained:raise
            if error is not None:run.record(收尾另有错误=repr(cleanup))
        if archived:
            try:run.record(结果收尾=compact_storage(output))
            except Exception as storage:
                run.record(结果收尾错误=repr(storage))
                if error is None:raise
    run.record(阶段='连续24窗口对照完成并归档退出',完整实验完成=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',default='joint-controlled-pool-tail-stream-v1-20261104')
    p.add_argument('--detach',action='store_true');p.add_argument('--check',action='store_true')
    p.add_argument('--ftl-audit',action='store_true');args=p.parse_args()
    require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法');admission(BASELINE)
    check_observation(PRIOR,args.ftl_audit);budget=preflight();occupied=check_baseline_budget(24<<30)
    budget.update(新增磁盘预算字节=24<<30,单台内存预算字节=14<<30,项目目录已占字节=occupied,
        说明='连续24窗口、四臂串行，只编译客户端；保留原磁盘、内存和总目录保护线')
    require(budget['data可用字节']>=50<<30 and budget['可用内存字节']>=30<<30,'原资源保护线不足')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧结果')
    if args.check:print(json.dumps(budget,ensure_ascii=False));return
    password=getpass.getpass('持续负载实验认证（仅内存）：');output.mkdir();scorer,target=prepare(output)
    stamp(output/'预注册计划.json',dict(来源=str(PRIOR),平台=profile8(),窗口=WINDOWS,
        每臂请求=REQUESTS,重复=2,顺序=['p1关闭','p2关闭','m1开启','m2开启'],
        阶段窗口=[6,12,18,24],阶段间='不关闭数据库，不挂载，不Reset，保留在线H历史和物理RC',
        观测='原批次确认后刷新日志缓冲，以真实文件字节切点核算维护；采样期日志/迁移交错即停止',
        RR=1024,GC启动百分比=20,候选='原资格',放置='固定K1',选区='Greedy',
        编译='扩展客户端并新增Logger只读观测方法；同ABI重编五个ZenFS单元，原维护正文及Flush/同步对象不变',
        新增磁盘预算字节=24<<30,单台内存预算字节=14<<30,
        预算说明='请求追踪长度翻倍，显式增加本轮预算；原24GiB磁盘、16GiB内存及200GiB目录保护线不变',
        完整设备命令追踪=args.ftl_audit,固定输入='引用原共享来源',资源=budget))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,scorer,target)
    finally:password=None


if __name__=='__main__':main()
