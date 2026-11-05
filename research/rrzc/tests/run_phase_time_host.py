"""一对普通/协同放置完整对照；共同Greedy，仅增加时间归因观测。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import tarfile
import tempfile
import threading
import time
import subprocess
from run_integrated_greedy_host import (prepare as reuse_prepare,PRIOR,BASELINE,BINARY,
    CalibrationRun,PARENT,ROOT,IMAGE_DIR,admission,check_baseline_budget,detach_supervisor,
    preflight,profile8,stamp,wait_stage,preserve_unverified_image,digest,require,collect)
from run_triggered_gc_host import stop_business
from result_storage import configure,compact,check_observation
from sampling_scope_bundle import closed
from phase_time_source import wrapper
from analyze_integrated_pool import arm,REQUESTS
from analyze_reproducibility import initial,request_comparison
from analyze_pool_dbbench import SEMANTIC
from analyze_phase_time import analyze as time_analysis,finish as finish_time

GUEST_MODULES=('phase_time_source.py','build_phase_time_guest.py','build_all_blocks_dbbench_guest.py',
               'build_all_blocks_guest.py','calibration_source.py')


def freeze_client_headers(old,source):
    headers={}
    for path in sorted(old.glob('rrzc_*.h')):
        require(path.is_file() and not path.is_symlink(),'客户端头文件不是普通文件')
        shutil.copyfile(path,source/path.name);headers[path.name]=digest(path)
    require(all(n in headers for n in ('rrzc_tls_seed.h','rrzc_front_order.h',
        'rrzc_io_trace.h','rrzc_sst_lifetime.h')),'来源客户端头文件不完整')
    for path in [old/'校准-db_bench_tool.cc',*[source/n for n in headers]]:
        for name in re.findall(r'^#include "(rrzc_[^"]+\.h)"',path.read_text(),re.M):
            require(name in headers or (old/'协同池副本/plugin/zenfs/fs'/name).is_file(),
                '客户端本地头文件闭包缺失：'+name)
    stamp(source/'原客户端头文件身份.json',headers)
    return headers


def prepare(output):
    scorer,target = reuse_prepare(output); source = output/'源码'
    old = closed(PRIOR)
    freeze_client_headers(old,source)
    names = ('诊断-flush_job.cc.o','诊断-sync_point.cc.o','诊断-sync_point_impl.cc.o')
    with tarfile.open(source/'计时支持对象.tar.gz','x:gz') as tar:
        for name in names: tar.add(old/name,arcname=name,recursive=False)
    stamp(source/'计时支持对象.json',{n:digest(old/n) for n in names})
    for name in GUEST_MODULES:
        shutil.copyfile(ROOT/'tests'/name,source/name)
    # 用实际将要传输的清单进行隔离导入；启动客机之前就排除漏部署依赖。
    with tempfile.TemporaryDirectory(prefix='rrzc-phase-import-') as temp:
        for name in GUEST_MODULES:shutil.copyfile(source/name,Path(temp)/name)
        subprocess.run(['python3','-I','-c',
            'import sys;sys.path.insert(0,sys.argv[1]);import build_phase_time_guest',temp],check=True)
    shutil.copyfile(ROOT/'model/rrzc_phase_time.h',source/'rrzc_phase_time.h')
    for path in (Path(__file__),ROOT/'analyze_phase_time.py'):
        shutil.copyfile(path,source/path.name)
    p = source/'run_pool_business_guest.sh'; p.write_text(wrapper(p.read_text()))
    subprocess.run(['bash','-n',str(p)],check=True)
    stamp(source/'本轮新增源码身份.json',{p.name:digest(p) for p in source.iterdir() if p.is_file()})
    return scorer,target


def sample_host(run,stop):
    # 只读procfs，按两秒采样；使用宿主单调时钟，不冒称与客机纳秒同源。
    with (run.output/'宿主连续资源.jsonl').open('x') as out:
        while not stop.is_set():
            row = dict(宿主单调纳秒=time.monotonic_ns(),阶段=run.state.get('阶段'),
                当前组=run.state.get('当前策略'),客机PID=run.state.get('当前客机PID'))
            for name in ('stat','meminfo','vmstat','diskstats','loadavg','pressure/cpu','pressure/io','pressure/memory'):
                try: row[name] = Path('/proc',name).read_text()
                except OSError as e: row[name] = repr(e)
            pid = run.state.get('当前客机PID')
            if pid:
                for name in ('stat','status','io','schedstat'):
                    try: row['客机进程/'+name] = Path('/proc',str(pid),name).read_text()
                    except OSError as e: row['客机进程/'+name] = repr(e)
            out.write(json.dumps(row,ensure_ascii=False)+'\n'); out.flush(); stop.wait(2)


def execute(args,password,output,scorer,target,overlap=False,completed_greedy=None,read_cost=False,end_to_end=False):
    run = CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.target=target;run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    run.state.update(标签=Path(target).name,宿主实验标签=args.label,客机工作目录=target,
        边界='一臂K0普通放置、一臂K1协同放置；共同Greedy，受控单前台12窗口；时间观测共同新增',
        GC启用=True,窗口=12,顺序=['p1普通Greedy','m1协同Greedy'])
    if overlap:
        run.state.update(前后台重叠=True,边界='单前台固定请求，异步Flush，独立后台及原10秒GC；只在收尾排空')
    configure(run,input_source=PRIOR,ftl_audit=args.ftl_audit)
    stop=threading.Event();sampler=None;error=None
    try:
        run.start();run.ssh(f'test ! -e {target} && mkdir {target}');run.deployed=True
        sampler=threading.Thread(target=sample_host,args=(run,stop),daemon=True);sampler.start()
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+[f'femu@127.0.0.1:{target}/'],180)
        run.record(阶段='恢复原软件及固定输入，重编独立计时副本')
        builder=('reuse_overlap_guest.py' if completed_greedy else 'build_overlap_guest.py') if overlap else 'build_phase_time_guest.py'
        if read_cost:builder='build_read_cost_guest.py'
        if end_to_end:builder='build_end_to_end_guest.py'
        build_proof='前后台重叠构建.json' if overlap else '区间计时构建.json'
        for name in ('restore_calibration_guest.py','reuse_flush_origin_guest.py','restore_pool_input_guest.py',builder):
            run.ssh(f'python3 {target}/{name} {target}',900 if name.startswith('build_') else 180)
        run.command(run.scp()+[f'femu@127.0.0.1:{target}/'+build_proof,str(output/build_proof)])
        require(json.loads((output/build_proof).read_text())['通过'],'计时构建未通过')
        for tag,k in ((('m1',1),) if completed_greedy else (('p1',0),('m1',1))):
            run.record(阶段=tag+'完整12窗口'+('前后台重叠' if overlap else '时间归因'),当前策略=tag,当前池规模=k)
            wait_stage(run,tag,'run_pool_business_guest.sh',3600,mode='greedy')
            path=collect(run,tag);report=arm(path,True,scorer,pool_k=k,concurrent=overlap)
            stamp(output/(tag+'-核验.json'),report)
            stamp(output/(tag+'-时间核验.json'),time_analysis(path,concurrent=overlap))
            if overlap:
                from analyze_overlap import validate
                stamp(output/(tag+'-业务收尾核验.json'),validate(path,report,end_to_end=end_to_end))
            if read_cost:
                from analyze_read_cost import analyze as analyze_reads
                stamp(output/(tag+'-读取成本核验.json'),analyze_reads(path))
            if end_to_end:
                from analyze_end_to_end import analyze as analyze_application
                stamp(output/(tag+'-应用端到端核验.json'),analyze_application(path))
            if tag=='m1':
                start=initial(output/'逐臂采证/p1',path)
                requests=request_comparison(output/'逐臂采证/p1',path)
                require(all(start[n]['相同'] for n in SEMANTIC) and requests['逻辑提交相同']
                    and requests['完整成功请求数']==[REQUESTS,REQUESTS],'同对起点或请求未通过')
                stamp(output/'同对起点请求核验.json',dict(起点=start,请求=requests))
            run.record(已完成=[*run.state.get('已完成',[]),tag])
    except BaseException as failure:
        error=failure;run.record(原始错误=repr(failure))
        if run.deployed:
            try:stop_business(run)
            except Exception as e:run.record(业务中断反馈=repr(e))
        raise
    finally:
        archived=False
        try:run.finish(error);archived=True
        except Exception as cleanup:
            retained=preserve_unverified_image(run,cleanup)
            if error is None and not retained:raise
            if error is not None:run.record(收尾另有错误=repr(cleanup))
        finally:
            stop.set()
            if sampler:sampler.join(timeout=5)
        if archived:
            try:run.record(结果收尾=compact(output))
            except Exception as storage:
                run.record(结果收尾错误=repr(storage))
                if error is None:raise
    if overlap:
        from analyze_overlap import finish as finish_overlap
        result=finish_overlap(output,end_to_end=end_to_end)
    else:result=finish_time(output)
    if end_to_end:
        from analyze_end_to_end import finish as finish_application
        finish_application(output)
    analysis_dir='前后台重叠分析' if overlap else '时间账目分析'
    run.record(阶段='两臂完整实验及时间账目完成，已归档退出',完整实验完成=True,
        时间账目={t:r['窗口分账'] for t,r in result.items()},自动分析=str(output/analysis_dir/'结果分析.md'))
    print('两臂实验完成，报告：'+str(output/analysis_dir/'结果分析.md'),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',default='joint-controlled-phase-time-v1-20261105')
    p.add_argument('--detach',action='store_true');p.add_argument('--check',action='store_true')
    p.add_argument('--ftl-audit',action='store_true',help='本轮明确诊断；两臂一致，沿用固定输入来源观测开关')
    args=p.parse_args();require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法')
    admission(BASELINE);check_observation(PRIOR,args.ftl_audit)
    budget=preflight();used=check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'资源保护线不足')
    budget.update(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30,目录已占字节=used)
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖已有结果')
    if args.check:print(json.dumps(budget,ensure_ascii=False));return
    password=getpass.getpass('完整时间归因实验认证（仅内存）：');output.mkdir()
    scorer,target=prepare(output)
    stamp(output/'预注册计划.json',dict(平台=profile8(),每臂请求=REQUESTS,窗口=12,
        顺序=['p1普通K0＋Greedy','m1协同K1＋Greedy'],额外Greedy重复组=False,
        RR阈值=1024,GC启动百分比=20,原候选资格=True,模型执行=False,
        原请求逻辑=True,缓存=False,WAL=False,MemTable=True,
        区间计时='内存缓冲，正常退出时写出；内部区间可嵌套；只按API/前台显式等待/其他分账',
        宿主采样秒=2,完整设备命令追踪=args.ftl_audit,资源=budget,输入来源=str(PRIOR),
        边界='同一新程序两臂完整对照；不将旧观测计时混入，不作正常并发或统计显著性结论'))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,scorer,target)
    finally:password=None


if __name__=='__main__':main()
