"""补齐固定K1与Greedy的完整业务；逐字复用已验收程序及共同逻辑输入。"""
import argparse
import getpass
import io
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile

from run_pool_dbbench_host import (BASELINE,DONOR,BINARY,CalibrationRun,PREVIOUS,PARENT,ROOT,
    IMAGE_DIR,admission,check_baseline_budget,detach_supervisor,freeze,freeze_bundle,preflight,
    profile8,stamp,wait_stage,stop_business,preserve_unverified_image,digest,require,collect,repeat_guard)
from flush_control_bundle import freeze as freeze_program
from sampling_scope_bundle import closed
from calibration_source import once
from analyze_integrated_pool import arm,REQUESTS
from analyze_reproducibility import initial,request_comparison
from analyze_pool_dbbench import SEMANTIC
from result_storage import configure as configure_storage, compact as compact_storage, replay_source, check_observation

PRIOR=IMAGE_DIR/'joint-controlled-integrated-pool-v2-20261104-results'


def prepare(output):
    old=closed(PRIOR)
    require(all(json.loads((PRIOR/(n+'重复核验.json')).read_text())['通过'] for n in ('Greedy','机制')),
            '完整来源重复尚未通过')
    freeze(output,restore_fix=True,reuse_build=PREVIOUS)
    reused=json.loads((BASELINE/'源码/复用诊断客户端.json').read_text())
    freeze_bundle(output,PREVIOUS,DONOR,reused,metadata_reference=False)
    scorer=freeze_program(output,prior=PRIOR);source=output/'源码'
    for name in ('restore_calibration_guest.py','reuse_flush_origin_guest.py','restore_pool_input_guest.py'):
        shutil.copyfile(ROOT/'tests'/name,source/name)
    # 运行辅助脚本也沿用来源字节，避免新的观察路径混入。
    for name in ('logical_input_guest.py','calibration_guest.py','strict_stats_guest.py','metadata_start_guest.py'):
        shutil.copyfile(PRIOR/'源码'/name,source/name)
    for name in ('受控标签.txt','受控标签来源.json'):
        shutil.copyfile(PRIOR/'源码'/name,source/name)
    shutil.copyfile(old/'元数据起点目标.json',source/'元数据起点目标.json')
    text=(PRIOR/'源码/run_pool_business_guest.sh').read_text()
    text=once(text,'pool_k=0\nif [[ $mode == blocks ]]; then pool_k=1; fi','pool_k=1')
    text=once(text,'"$target/p1" matched','"$target/参考起点" matched')
    (source/'run_pool_business_guest.sh').write_text(text)
    subprocess.run(['bash','-n',str(source/'run_pool_business_guest.sh')],check=True)
    # 在全新临时客机中保持原绝对路径，原始输入清单及其SHA256无需改写。
    reference=replay_source(PRIOR);prefix=reference['label']+'/逻辑输入/'
    files={};payload=source/'固定输入复用.tar'
    with tarfile.open(reference['archive']) as original,tarfile.open(payload,'x') as out:
        for m in original:
            if not m.name.startswith(prefix) or not m.isfile():continue
            relative='逻辑输入/'+m.name[len(prefix):]
            require('..' not in Path(relative).parts and relative not in files,'输入路径不合法')
            data=original.extractfile(m).read();info=tarfile.TarInfo(relative);info.size=len(data);info.mode=0o644
            out.addfile(info,io.BytesIO(data))
            import hashlib
            files[relative]=hashlib.sha256(data).hexdigest()
        for name in ('装载-0.sst.csv','装载-0.extent.csv','zones.before.json','space.before.json',
                     'blocks.before.json','真实准备读取.json','逻辑输入回放.json'):
            p=PRIOR/'逐臂采证/p1'/name;relative='参考起点/'+name
            out.add(p,arcname=relative,recursive=False);files[relative]=digest(p)
    require(sum(n.endswith('.bin.gz') for n in files)==43,'完整装载输入缺失')
    stamp(source/'固定输入复用清单.json',dict(来源=str(PRIOR),文件=files,
        压缩包SHA256=digest(payload),客机工作目录='/home/femu/'+reference['label'],
        来源归档SHA256=reference['sha256']))
    return scorer,'/home/femu/'+reference['label']


def compare_start(a,b):
    start=initial(a,b);requests=request_comparison(a,b)
    require(all(start[k]['相同'] for k in SEMANTIC) and requests['逻辑提交相同'],
            '跨轮起点或固定逻辑请求不一致')
    return dict(起点=start,请求=requests)


def execute(args,password,output,scorer,target):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.target=target;run.state.update(标签=Path(target).name,宿主实验标签=args.label,
        客机工作目录=target,边界='新客机复用原绝对路径以保持固定输入字节；非旧客机复用',
        GC启用=True,固定K=1,选区='Greedy',原模型只观察=True)
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    configure_storage(run,input_source=PRIOR,ftl_audit=args.ftl_audit)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {target} && mkdir {target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+[f'femu@127.0.0.1:{target}/'],180)
        run.record(阶段='逐字恢复完整机制程序和原始逻辑输入，不重新编译')
        run.ssh(f'python3 {target}/restore_calibration_guest.py {target}',180)
        run.ssh(f'python3 {target}/reuse_flush_origin_guest.py {target}',180)
        run.ssh(f'python3 {target}/restore_pool_input_guest.py {target}',180)
        run.command(run.scp()+[f'femu@127.0.0.1:{target}/生命周期构建核验.json',str(output/'生命周期构建核验.json')])
        build=json.loads((output/'生命周期构建核验.json').read_text())
        require(build['在线分类版本']==2 and build['本轮逐字复用'],'复用程序不符')
        for tag in ('p1','p2'):
            run.record(阶段=tag+'协同K1与Greedy完整业务')
            wait_stage(run,tag,'run_pool_business_guest.sh',3600,mode='greedy')
            path=collect(run,tag);report=arm(path,True,scorer,pool_k=1)
            stamp(output/(tag+'-核验.json'),report)
            stamp(output/(tag+'-跨轮起点请求核验.json'),compare_start(PRIOR/'逐臂采证/p1',path))
            run.record(已完成=[*run.state.get('已完成',[]),tag])
        repeat=repeat_guard(output/'逐臂采证/p1',output/'逐臂采证/p2',request_count=REQUESTS)
        stamp(output/'协同Greedy重复核验.json',repeat)
        require(repeat['通过'],'协同Greedy重复有分歧，不能发布收益')
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
    result={}
    for tag in ('p1','p2'):
        current=output/'逐臂采证'/tag
        model=PRIOR/'逐臂采证'/('m1' if tag=='p1' else 'm2')
        result[tag]=dict(报告=json.loads((output/(tag+'-核验.json')).read_text()),
                        与原协同模型逐事件比较=repeat_guard(model,current,request_count=REQUESTS))
    stamp(output/'贡献拆分核验.json',dict(来源=str(PRIOR),协同Greedy=result,
        边界='两次固定种子工程重复；性能受运行时波动影响，不作正式显著性结论'))
    run.record(阶段='协同Greedy完整对照完成，已归档和退出',完整实验完成=True,
               与原模型事件完全相同={k:v['与原协同模型逐事件比较']['通过'] for k,v in result.items()})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',default='joint-controlled-integrated-greedy-v1-20261104')
    p.add_argument('--detach',action='store_true');p.add_argument('--check',action='store_true')
    p.add_argument('--ftl-audit',action='store_true',help='显式诊断才记录完整设备命令；改变观测开销，需独立重复核验')
    args=p.parse_args()
    require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法');admission(BASELINE)
    check_observation(PRIOR,args.ftl_audit)
    budget=preflight();check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'资源保护线不足')
    budget.update(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30,
        说明='输入传输后引用共享检查点，归档只保留关键证据；保留24GiB磁盘和16GiB内存保护线')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖已有结果')
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('协同Greedy完整对照认证（仅内存）：');output.mkdir()
    scorer,target=prepare(output)
    stamp(output/'预注册计划.json',dict(来源=str(PRIOR),平台=profile8(),固定K=1,选区='Greedy',
        总请求=REQUESTS,重复=2,不重新编译=True,复用原逻辑输入=True,
        只改选区执行开关=True,RR=1024,GC启动百分比=20,候选资格='原资格',资源=budget,
        客机绝对路径复用=target,完整设备命令追踪=args.ftl_audit,固定输入保留='引用来源，禁止重复归档输入字节',
        说明='全新隔离客机；仅保持工作路径以复用原始清单，不是NAND快照'))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,scorer,target)
    finally:password=None


if __name__=='__main__':main()
