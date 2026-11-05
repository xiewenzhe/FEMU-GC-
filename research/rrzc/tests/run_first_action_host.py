"""首区模型的完整24窗口对照；尾部关闭，共同观察，原Greedy先重复。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess

from run_pool_tail_stream_host import prepare as stream_prepare,stop_stream
from run_pool_tail_goal_host import PRIOR
from run_integrated_greedy_host import (BASELINE,BINARY,CalibrationRun,PARENT,ROOT,IMAGE_DIR,
    admission,check_baseline_budget,detach_supervisor,preflight,profile8,stamp,wait_stage,
    preserve_unverified_image,digest,require,collect,repeat_guard)
from result_storage import configure as configure_storage,compact as compact_storage,check_observation
from analyze_integrated_pool import arm
from analyze_first_action import decisions,finish
from analyze_reproducibility import initial,request_comparison
from analyze_pool_dbbench import SEMANTIC
from first_action_source import wrapper


def prepare(output):
    scorer,target=stream_prepare(output);source=output/'源码'
    p=source/'run_pool_business_guest.sh';p.write_text(wrapper(p.read_text()))
    subprocess.run(['bash','-n',str(p)],check=True)
    for n in ('first_action_source.py','build_first_action_guest.py'):
        shutil.copyfile(ROOT/'tests'/n,source/n)
    shutil.copyfile(ROOT/'model/rrzc_first_action.h',source/'rrzc_first_action.h')
    for n,p in [('首区宿主控制.py',Path(__file__)),('首区分析源码.py',ROOT/'analyze_first_action.py')]:
        shutil.copyfile(p,source/n)
    return scorer,target


def execute(args,password,output,scorer,target):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.target=target;run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    run.state.update(标签=Path(target).name,宿主实验标签=args.label,客机工作目录=target,
        固定K=1,尾部执行=False,GC启用=True,窗口=24,边界='新程序共同观测，Greedy先独立重复；模型输入随实际墙钟变化')
    configure_storage(run,input_source=PRIOR,ftl_audit=args.ftl_audit);error=None
    try:
        run.start();run.ssh(f'test ! -e {target} && mkdir {target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+[f'femu@127.0.0.1:{target}/'],180)
        run.record(阶段='构建首区模型与共同文件观察，五单元及客户端同ABI编译')
        for script in ('restore_calibration_guest.py','reuse_flush_origin_guest.py','restore_pool_input_guest.py','build_first_action_guest.py'):
            run.ssh(f'python3 {target}/{script} {target}',300)
        run.command(run.scp()+[f'femu@127.0.0.1:{target}/首区模型构建.json',str(output/'首区模型构建.json')])
        for tag in ('p1','p2','m1','m2'):
            greedy=tag.startswith('p');run.record(阶段=tag+'首区模型完整24窗口业务',执行Greedy=greedy)
            wait_stage(run,tag,'run_pool_business_guest.sh',3600,mode='greedy' if greedy else 'blocks')
            path=collect(run,tag);report=arm(path,greedy,scorer,pool_k=1,windows=24,decision_reader=decisions)
            text=(path/'run.zenfs.log').read_text()
            require('首区模型配置：版本=1，启用=1' in text and '协同尾部配置：版本=1，执行=0' in text,'真实配置不符')
            stamp(output/(tag+'-核验.json'),report)
            if tag!='p1':
                a=initial(output/'逐臂采证/p1',path);b=request_comparison(output/'逐臂采证/p1',path)
                require(all(a[k]['相同'] for k in SEMANTIC) and b['逻辑提交相同']
                    and b['完整成功请求数']==[4521984]*2,'起点或完整请求不同')
                stamp(output/(tag+'-起点请求核验.json'),dict(起点=a,请求=b))
            run.record(已完成=[*run.state.get('已完成',[]),tag])
            if tag in ('p2','m2'):
                first=output/'逐臂采证'/('p1' if greedy else 'm1')
                proof=repeat_guard(first,path,request_count=4521984)
                stamp(output/(('Greedy' if greedy else '模型')+'重复核验.json'),proof)
                if greedy:require(proof['通过'],'共同观测Greedy重复分歧，不进入模型收益比较')
                else:run.record(模型重复通过=proof['通过'],重复边界='若不同保留两臂和真实输入，不固定victim强求一致')
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
    run.record(阶段='首区模型完整业务完成，已归档退出',完整实验完成=True)
    result=finish(output,output/'完整实验分析')
    run.record(阶段='首区模型完整对照分析完成',结果={t:dict(维护=r['维护'],改选次数=r['改选次数']) for t,r in result['各臂'].items()})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',default='joint-controlled-first-action-v1-20261105')
    p.add_argument('--detach',action='store_true');p.add_argument('--check',action='store_true')
    p.add_argument('--ftl-audit',action='store_true');args=p.parse_args()
    require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法')
    admission(BASELINE);check_observation(PRIOR,args.ftl_audit);budget=preflight()
    occupied=check_baseline_budget(24<<30)
    require(budget['data可用字节']>=50<<30 and budget['可用内存字节']>=30<<30,'原资源保护线不足')
    budget.update(新增磁盘预算字节=24<<30,单台内存预算字节=14<<30,目录已占字节=occupied)
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖已有实验')
    if args.check:print(json.dumps(budget,ensure_ascii=False));return
    password=getpass.getpass('首区模型实验认证（仅内存）：');output.mkdir();scorer,target=prepare(output)
    stamp(output/'预注册计划.json',dict(平台=profile8(),每臂请求=4521984,窗口=24,重复=2,
        顺序=['p1协同Greedy','p2协同Greedy','m1首区模型','m2首区模型'],尾部复用=False,固定K=1,
        评分='有效字节减热文件折扣RR效用，再除净回收字节；所有共同安全候选直接排名',
        RR效用='单文件区内字节乘min(连续观察存活,30秒)/(min(连续观察存活,30秒)+最早同块ETA)',
        存活条件='当前在线H、同逻辑文件字节稳定、至少2秒连续可见；消失或大小改变重新观察',
        预测='同块近期2到4秒及十秒窗口中较慢速率，区内取最早ETA；不强求ETA位于观察窗口内',
        校准边界='30秒为首版预注册效用尺度，不是概率、经过校准寿命或实际可避免RR金额',
        原规则=dict(RR=1024,GC启动百分比=20,候选='original'),缓存='关闭，WAL关闭，MemTable保留',
        串行=True,完整设备追踪=args.ftl_audit,资源=budget,输入来源=str(PRIOR)))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,scorer,target)
    finally:password=None


if __name__=='__main__':main()
