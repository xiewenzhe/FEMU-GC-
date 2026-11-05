"""复用尾部原程序，两臂重现完整业务后按原规则结算共同空间下限。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile

from run_integrated_greedy_host import (prepare as base_prepare,PRIOR as INPUT_SOURCE,
    BASELINE,BINARY,CalibrationRun,PARENT,ROOT,IMAGE_DIR,admission,check_baseline_budget,
    detach_supervisor,preflight,profile8,stamp,wait_stage,stop_business,preserve_unverified_image,
    digest,require,collect,repeat_guard,arm,REQUESTS)
from sampling_scope_bundle import closed
from result_storage import configure as configure_storage,compact as compact_storage,check_observation
from pool_tail_goal_source import wrapper,smoke,GOAL

PRIOR=IMAGE_DIR/'joint-controlled-pool-tail-v3-20261104-results'


def prepare(output):
    old=closed(PRIOR)
    require(all(json.loads((PRIOR/(n+'重复核验.json')).read_text())['通过'] for n in ('关闭','开启')),
        '尾部来源重复未通过')
    require(json.loads((PRIOR/'p1-核验.json').read_text())['末端空间']['空闲字节']==GOAL,'预定目标与来源不符')
    scorer,target=base_prepare(output);source=output/'源码'
    # 只覆盖新建传输目录内的旧恢复包；来源已归档文件一律只读。
    identity=json.loads((old/'生命周期构建核验.json').read_text())
    plan=json.loads((old/'冻结对象清单.json').read_text())
    require(digest(old/'db_bench')==identity['程序SHA256']
        and digest(old/'fs_colocation.o')==plan['对象SHA256']==identity['冻结ZenFS对象SHA256']
        and digest(old/'冻结对象.tar.gz')==plan['压缩包SHA256'],'来源程序身份不同')
    for n in ('冻结对象.tar.gz','冻结对象清单.json'):shutil.copyfile(old/n,source/n)
    names=('db_bench','space_probe','flush_metadata_probe','生命周期构建核验.json',
        '校准-db_bench_tool.cc','诊断-flush_job.cc','rrzc_sst_lifetime.h')
    payload=source/'Flush来源复用.tar.gz'
    with tarfile.open(payload,'w:gz') as tar:
        for n in names:tar.add(old/n,arcname=n,recursive=False)
    stamp(source/'Flush来源复用清单.json',dict(来源=str(PRIOR),文件={n:digest(old/n) for n in names},
        压缩包SHA256=digest(payload),对象SHA256=plan['对象SHA256'],原库SHA256=plan['原库SHA256'],
        程序SHA256=identity['程序SHA256'],请求源码SHA256=identity['新客户端源码SHA256'],
        来源完整归档SHA256=json.loads((PRIOR/'状态.json').read_text())['镜像处理']['archive_sha256']))
    for n in ('build_pool_tail_goal_guest.py','pool_tail_goal_device.cc','build_all_blocks_guest.py'):
        shutil.copyfile(ROOT/'tests'/n,source/n)
    shutil.copyfile(old/'rrzc_front_order.h',source/'rrzc_front_order.h')
    text=wrapper((PRIOR/'源码/run_pool_business_guest.sh').read_text())
    for n,s in [('run_pool_business_guest.sh',text),('run_goal_smoke_guest.sh',smoke(text))]:
        (source/n).write_text(s);subprocess.run(['bash','-n',str(source/n)],check=True)
    return scorer,target


def execute(args,password,output,scorer,target):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.target=target;run.state.update(标签=Path(target).name,宿主实验标签=args.label,
        客机工作目录=target,GC启用=True,固定K=1,选区='Greedy',共同空间下限=GOAL,
        边界='原业务逐字复用并核验历史两次；新增收尾仅一对实际路径，不声称正式重复或完全相等终态')
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    configure_storage(run,input_source=INPUT_SOURCE,ftl_audit=args.ftl_audit);error=None
    try:
        run.start();run.ssh(f'test ! -e {target} && mkdir {target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+[f'femu@127.0.0.1:{target}/'],180)
        run.record(阶段='恢复原尾部程序和共同输入，仅编译结算工具')
        for script in ('restore_calibration_guest.py','reuse_flush_origin_guest.py','restore_pool_input_guest.py','build_pool_tail_goal_guest.py'):
            run.ssh(f'python3 {target}/{script} {target}',180)
        run.record(阶段='空间结算工具真实挂载及已达标退出检查')
        wait_stage(run,'goal-smoke','run_goal_smoke_guest.sh',120,mode='greedy')
        rows=[json.loads(l) for l in run.ssh(f'cat {target}/goal-smoke/goal.log').splitlines() if l.startswith('{')]
        require(len(rows)==1 and rows[0]['达标'] and rows[0]['序号']==0,'结算小测失败')
        stamp(output/'结算工具真实小测.json',dict(通过=True,采样=rows,边界='挂载采样和已达标退出，不代替实际补空间测试'))
        for tag in ('p1','m1'):
            run.record(阶段=tag+'完整业务及空间结算',尾部复用执行=tag.startswith('m'))
            wait_stage(run,tag,'run_pool_business_guest.sh',3600,mode='greedy')
            path=collect(run,tag);report=arm(path,True,scorer,pool_k=1)
            stamp(output/(tag+'-核验.json'),report)
            proof=repeat_guard(PRIOR/'逐臂采证'/tag,path,request_count=REQUESTS)
            stamp(output/(tag+'-原业务复现核验.json'),proof)
            require(proof['通过'],'原业务未重现，停止净收益比较')
            from analyze_pool_tail_goal import goal_arm
            stamp(output/(tag+'-空间结算核验.json'),goal_arm(path,report))
            run.record(已完成=[*run.state.get('已完成',[]),tag])
    except BaseException as failed:
        error=failed;run.record(原始错误=repr(failed))
        if run.deployed:
            try:stop_business(run)
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
    run.record(阶段='完整业务空间结算已完成并归档退出',完整实验完成=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',default='joint-controlled-pool-tail-goal-v1-20261104')
    p.add_argument('--detach',action='store_true');p.add_argument('--check',action='store_true')
    p.add_argument('--ftl-audit',action='store_true');args=p.parse_args()
    require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法');admission(BASELINE)
    check_observation(PRIOR,args.ftl_audit);budget=preflight();check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'原资源保护线不足')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧结果')
    if args.check:print(json.dumps(budget,ensure_ascii=False));return
    password=getpass.getpass('空间结算实验认证（仅内存）：');output.mkdir();scorer,target=prepare(output)
    stamp(output/'预注册计划.json',dict(来源=str(PRIOR),平台=profile8(),共同最低空闲字节=GOAL,
        目标来源='原关闭组两次共同末端空闲，启动前固定',业务请求=REQUESTS,RR=1024,GC=20,
        顺序=['p1关闭','m1开启'],收尾='无前台输入、共同Greedy，保留原触发及候选，记录超额空间；两次无进展停止',
        复用原程序和ZenFS=True,业务复现='必须分别核对上一轮已重复通过的同臂全部事件和账目',
        重复边界='新增结算暂为一对实际路径，不冒称独立重复及统计显著性',新增磁盘预算字节=16<<30,
        单台内存预算字节=14<<30,完整设备命令追踪=args.ftl_audit,固定输入='引用原共享来源'))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,scorer,target)
    finally:password=None


if __name__=='__main__':main()
