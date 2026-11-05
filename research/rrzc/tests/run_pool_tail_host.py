"""完整K1＋Greedy尾部管理关闭/开启各两次；原逻辑输入及安全规则保留。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess

from run_integrated_greedy_host import (prepare as base_prepare,PRIOR,compare_start,
    BASELINE,BINARY,CalibrationRun,PARENT,ROOT,IMAGE_DIR,admission,check_baseline_budget,
    detach_supervisor,preflight,profile8,stamp,wait_stage,stop_business,preserve_unverified_image,
    digest,require,collect,repeat_guard,arm,REQUESTS)
from sampling_scope_bundle import closed
from calibration_source import once
from result_storage import configure as configure_storage, compact as compact_storage, check_observation


def prepare(output):
    scorer,target=base_prepare(output);source=output/'源码';old=closed(PRIOR)
    for n in ('build_pool_tail_guest.py','pool_tail_source.py','build_flush_origin_guest.py',
              'build_pool_dbbench_guest.py','build_all_blocks_guest.py','build_all_blocks_dbbench_guest.py',
              'flush_wait_source.py','restore_pool_input_guest.py','build_sst_interleaved_guest.py',
              'sst_interleaved_source.py','sst_lifetime_source.py','flush_origin_source.py','calibration_source.py',
              'pool_tail_device.cc','colocation_device.cc','run_pool_tail_smoke.sh'):
        shutil.copyfile(ROOT/'tests'/n,source/n)
    for n in ('rrzc_pool_tail.h','rrzc_pool_tail_zenfs.inc'):shutil.copyfile(ROOT/'model'/n,source/n)
    for p in old.glob('rrzc*.h'):shutil.copyfile(p,source/p.name)
    for n in ('诊断-sync_point.cc','诊断-sync_point_impl.cc','dbbench_space_probe.cc'):
        shutil.copyfile(old/n,source/n)
    p=source/'run_pool_business_guest.sh';text=p.read_text()
    text=once(text,'flush_w=0\nrun_bench() {',
        'tail_on=0\nif [[ $arm_tag == m* ]]; then tail_on=1; fi\nflush_w=0\nrun_bench() {')
    text=once(text,'ZENFS_RRZC_STREAM_CLASSIFY=1',
        'ZENFS_RRZC_POOL_TAIL_OBSERVE=1 ZENFS_RRZC_POOL_TAIL_REUSE="$tail_on" ZENFS_RRZC_STREAM_CLASSIFY=1')
    p.write_text(text);subprocess.run(['bash','-n',str(p)],check=True)
    return scorer,target


def execute(args,password,output,scorer,target):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.target=target;run.state.update(标签=Path(target).name,宿主实验标签=args.label,
        客机工作目录=target,GC启用=True,固定K=1,选区='Greedy',尾部对照=True)
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2');error=None
    # 本轮两组同观测开销；历史运行仅核对事件，不能混用旧计时。
    configure_storage(run,input_source=PRIOR,ftl_audit=args.ftl_audit)
    try:
        run.start();run.ssh(f'test ! -e {target} && mkdir {target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+[f'femu@127.0.0.1:{target}/'],180)
        run.record(阶段='恢复旧程序和共同逻辑输入，编译独立尾部管理副本')
        for script in ('restore_calibration_guest.py','reuse_flush_origin_guest.py','restore_pool_input_guest.py','build_pool_tail_guest.py'):
            run.ssh(f'python3 {target}/{script} {target}',900 if script.startswith('build') else 180)
        run.command(run.scp()+[f'femu@127.0.0.1:{target}/生命周期构建核验.json',str(output/'生命周期构建核验.json')])
        run.record(阶段='一次真实设备安全检查：原WP续写、内容、重挂载和归零退出')
        run.root(f'bash {target}/run_pool_tail_smoke.sh',120)
        require('尾部真实安全检查通过' in run.ssh(f'cat {target}/tail-smoke/run.log'),'尾部安全检查失败')
        for tag in ('p1','p2','m1','m2'):
            enabled=tag.startswith('m');run.record(阶段=tag+'完整业务',尾部复用执行=enabled)
            wait_stage(run,tag,'run_pool_business_guest.sh',3600,mode='greedy')
            path=collect(run,tag);report=arm(path,True,scorer,pool_k=1)
            log=(path/'run.zenfs.log').read_text()
            require(f'协同尾部配置：版本=1，执行={int(enabled)}，观测=1' in log,'尾部开关不符')
            require('协同尾部关闭清账：成功=1' in log,'关闭清账缺失或失败')
            stamp(output/(tag+'-核验.json'),report)
            stamp(output/(tag+'-起点请求核验.json'),compare_start(PRIOR/'逐臂采证/p1',path))
            run.record(已完成=[*run.state.get('已完成',[]),tag])
            if tag=='p1':
                proof=repeat_guard(PRIOR/'逐臂采证/m1',path,request_count=REQUESTS)
                stamp(output/'关闭开关原行为核验.json',proof)
                require(proof['通过'],'关闭开关未保持原行为，停止收益比较')
            if tag in ('p2','m2'):
                proof=repeat_guard(output/'逐臂采证'/('p1' if tag=='p2' else 'm1'),path,request_count=REQUESTS)
                stamp(output/(('关闭' if tag=='p2' else '开启')+'重复核验.json'),proof)
                require(proof['通过'],'同配置完整重复有分歧')
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
    reports={t:json.loads((output/(t+'-核验.json')).read_text()) for t in ('p1','p2','m1','m2')}
    stamp(output/'完整尾部对照.json',dict(各臂=reports,边界='固定种子受控完整业务，不宣称正常并发性能'))
    run.record(阶段='完整尾部对照完成，已归档和退出',完整实验完成=True,
        结果={t:r['维护'] for t,r in reports.items()})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',default='joint-controlled-pool-tail-v1-20261104')
    p.add_argument('--detach',action='store_true');p.add_argument('--check',action='store_true')
    p.add_argument('--ftl-audit',action='store_true',help='显式诊断追踪，必须与来源观测口径一致')
    args=p.parse_args()
    require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法');admission(BASELINE)
    check_observation(PRIOR,args.ftl_audit)
    budget=preflight();check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'资源保护线不足')
    budget.update(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30)
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧结果')
    if args.check:print(json.dumps(budget,ensure_ascii=False));return
    password=getpass.getpass('尾部管理完整对照认证（仅内存）：');output.mkdir();scorer,target=prepare(output)
    stamp(output/'预注册计划.json',dict(平台=profile8(),来源=str(PRIOR),固定K=1,选区='Greedy',
        总请求=REQUESTS,重复=2,顺序=['p1关闭','p2关闭','m1开启','m2开启'],
        RR=1024,GC启动百分比=20,候选资格='原资格',原逻辑输入复用=True,资源=budget,
        完整设备命令追踪=args.ftl_audit,固定输入保留='引用来源，归档不重复输入字节',
        规则='只保留池内零有效可写尾部；风险未知、资源压力和关闭时正常退出，不新增搬迁'))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,scorer,target)
    finally:password=None


if __name__=='__main__':main()
