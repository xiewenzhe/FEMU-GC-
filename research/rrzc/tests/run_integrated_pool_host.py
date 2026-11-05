"""完整初版直接跑真实RocksDB对照；不通过局部正收益筛选后才允许机制运行。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess

from run_pool_dbbench_host import (BASELINE,DONOR,BINARY,CalibrationRun,PREVIOUS,PARENT,ROOT,
    IMAGE_DIR,admission,check_baseline_budget,detach_supervisor,freeze,freeze_bundle,preflight,
    profile8,stamp,wait_stage,stop_business,preserve_unverified_image,digest,require,collect,repeat_guard)
from run_sst_interleaved_host import freeze_sampling
from sampling_scope_bundle import closed
from analyze_reproducibility import initial,request_comparison
from analyze_pool_dbbench import SEMANTIC
from analyze_integrated_pool import arm,finish,REQUESTS
from integrated_pool_source import source,business
from result_storage import configure as configure_storage, compact as compact_storage
from result_storage import check_checkpoint_capacity, reserve_checkpoint

REFERENCE=IMAGE_DIR/'joint-controlled-sst-flush-w-v3-20261030-results'


def prepare(output):
    freeze(output,restore_fix=True,reuse_build=PREVIOUS)
    reused=json.loads((BASELINE/'源码/复用诊断客户端.json').read_text())
    freeze_bundle(output,PREVIOUS,DONOR,reused,metadata_reference=False)
    names=('restore_calibration_guest.py','build_pool_dbbench_guest.py','build_all_blocks_guest.py',
        'build_all_blocks_dbbench_guest.py','flush_wait_source.py','calibration_source.py','dbbench_space_probe.cc',
        'logical_input_guest.py','calibration_guest.py','strict_stats_guest.py','metadata_start_guest.py',
        'sst_lifetime_source.py','rrzc_sst_lifetime.h','sst_interleaved_source.py','build_sst_interleaved_guest.py',
        'build_flush_origin_guest.py','flush_origin_source.py','flush_metadata_probe.cc',
        'stream_profile_source.py','integrated_pool_guest.py','pool_dbbench_guest.py')
    for name in names:shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    for name in ('rrzc_stream_profile.h','rrzc_flush_origin.h'):
        shutil.copyfile(ROOT/'model'/name,output/'源码'/name)
    native=DONOR/DONOR.name.removesuffix('-results')
    for name in ('rrzc_tls_seed.h','rrzc_front_order.h','rrzc_io_trace.h','rrzc_boundary_sync.h',
                 '校准-db_bench_tool.cc','诊断-flush_job.cc','诊断-sync_point.cc','诊断-sync_point_impl.cc'):
        shutil.copyfile(native/name,output/'源码'/name)
    old=closed(REFERENCE);build=json.loads((old/'生命周期构建核验.json').read_text())
    for name,fn in (('run_pool_source_guest.sh',source),('run_pool_business_guest.sh',business)):
        text=fn((REFERENCE/'源码'/name).read_text());path=output/'源码'/name;path.write_text(text)
        subprocess.run(['bash','-n',str(path)],check=True)
    (output/'源码/启用在线协同机制构建').write_text('无手工H标签；完整机制直接对照，K固定1，W为Flush来源代理\n')
    (output/'源码/受控标签.txt').write_text('')
    stamp(output/'源码/受控标签来源.json',dict(标签={},说明='实际业务不依赖文件名单或未来退役'))
    (output/'源码/启用本轮元数据参考').write_text('以p1本轮实际元数据RC建立参考\n')
    (output/'源码/元数据参考首臂.txt').write_text('p1\n')
    stamp(output/'源码/Flush来源构建计划.json',dict(请求源码SHA256=build['新客户端源码SHA256'],
        原对象SHA256=build['冻结ZenFS对象SHA256'],计划头将在构建时明确切换=True))
    return freeze_sampling(output)


def execute(args,password,output,scorer):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    configure_storage(run,ftl_audit=args.ftl_audit)
    run.state.update(边界='真实RocksDB完整机制直接对照，非局部机会小测；受控单前台',GC启用=True,
                     在线分类=True,固定K=1,共同初始放置='普通K0',H手工标签=False)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+[f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='完整编译在线分类和协同放置，原模型冻结')
        run.ssh(f'python3 {run.target}/restore_calibration_guest.py {run.target}',180)
        run.ssh(f'python3 {run.target}/build_flush_origin_guest.py {run.target}',900)
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/生命周期构建核验.json',str(output/'生命周期构建核验.json')])
        build=json.loads((output/'生命周期构建核验.json').read_text())
        require(build['在线分类版本']==2 and build['ZenFS完整五单元重编'],'完整机制构建未通过')
        run.record(阶段='普通放置共同装载4GiB逻辑数据')
        wait_stage(run,'source','run_pool_source_guest.sh',1800,mode='greedy')
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/真实SST布局核验.json',str(output/'真实SST布局核验.json')])
        for tag in ('p1','p2','m1','m2'):
            greedy=tag.startswith('p');run.record(阶段=tag+'完整业务',当前策略='普通Greedy' if greedy else '在线协同与cost-benefit')
            wait_stage(run,tag,'run_pool_business_guest.sh',3600,mode='greedy' if greedy else 'blocks')
            path=collect(run,tag);report=arm(path,greedy,scorer)
            if tag!='p1':
                start=initial(output/'逐臂采证/p1',path);request=request_comparison(output/'逐臂采证/p1',path)
                require(all(start[k]['相同'] for k in SEMANTIC) and request['逻辑提交相同'],'已采集起点或业务请求不同')
            stamp(output/(tag+'-核验.json'),report)
            run.record(已完成=[*run.state.get('已完成',[]),tag])
            if tag in ('p2','m2'):
                proof=repeat_guard(output/'逐臂采证'/('p1' if greedy else 'm1'),path,request_count=REQUESTS)
                stamp(output/(('Greedy' if greedy else '机制')+'重复核验.json'),proof)
                if greedy:require(proof['通过'],'完整Greedy重复分歧，不能发布机制收益')
                else:run.record(机制重复完全相同=proof['通过'],说明='不同则报告逐臂差异，不以均值掩盖')
    except BaseException as failed:
        error=failed;run.record(原始错误=repr(failed))
        if run.deployed:
            try:stop_business(run)
            except Exception as stopped:run.record(业务中断反馈=repr(stopped))
        raise
    finally:
        archived=False
        try:
            run.finish(error);archived=True
        except Exception as cleanup:
            retained=preserve_unverified_image(run,cleanup)
            if error is None and not retained:raise
            if error is not None:run.record(收尾另有错误=repr(cleanup))
        if archived:
            try:run.record(结果收尾=compact_storage(output))
            except Exception as storage:
                run.record(结果收尾错误=repr(storage))
                if error is None:raise
    result=finish(output,output/'完整实验分析')
    run.record(阶段='完整机制对照完成，已归档和退出',完整实验完成=True,
               结果={k:dict(维护=v['维护'],改选次数=v['改选次数'],H晋升=v['在线H晋升次数']) for k,v in result['各臂'].items()})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',default='joint-controlled-integrated-pool-v2-20261104')
    p.add_argument('--check',action='store_true');p.add_argument('--detach',action='store_true')
    p.add_argument('--ftl-audit',action='store_true',help='显式诊断才记录完整设备命令；改变观测开销，需独立重复核验')
    args=p.parse_args()
    require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法');admission(BASELINE)
    check_checkpoint_capacity(IMAGE_DIR)
    budget=preflight();check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'资源保护线不足')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧结果')
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('完整机制实验认证（仅内存）：');output.mkdir()
    reserve_checkpoint(IMAGE_DIR,args.label,output);scorer=prepare(output)
    stamp(output/'预注册计划.json',dict(平台=profile8(),初始记录=1048576,逻辑值字节=4<<30,
        请求读=786432,请求写=1474560,总请求=REQUESTS,轮数=12,写键窗口='前六个65536键窗口循环两遍，保留4096个读热点键',
        H识别='约6.4万次SST底层读写窗口；256读/128页重访，或至少128读且累计读满文件四遍',
        W识别='真实Flush来源代理，不使用未来退役信息',固定K=1,自适应K=False,
        基线='普通K0放置+Greedy',机制='在线H/W协同K1放置+冻结同空间目标cost-benefit',
        顺序=['共同普通装载','完整Greedy-p1','完整Greedy-p2','完整机制-m1','完整机制-m2'],
        RR=1024,GC启动百分比=20,候选范围='原资格',缓存='关闭Block/Row/Persistent，DirectIO，WAL关闭，MemTable保留',
        单台串行=True,资源=budget,不以局部收益门控=True,单文件日志上限MiB=512,
        完整设备命令追踪=args.ftl_audit,固定输入保留='共享检查点，最多登记2份',
        边界='公开负载前的真实RocksDB定制db_bench完整对照；受控单前台，不声称正常并发稳态或统计显著性'))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,scorer)
    finally:password=None


if __name__=='__main__':main()
