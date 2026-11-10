"""第六阶段一对完整业务；原G2与W-only迁移门控，共同程序和aux观察。"""
import argparse
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import tarfile
import time
from types import SimpleNamespace
from run_placement_ablation_host import (record,parent_identity,normalize_permissions,interrupt,capture_unsealed_logs,
    CalibrationRun,PARENT,BINARY,configure,compact,replay_source,reserve_checkpoint,atomic_json,closed_archive,
    stamp,digest,require,ROOT,IMAGE_DIR,profile8,detach_supervisor,disk_space,check_baseline_budget,preflight)
from placement_business_source import wrapper as old_wrapper
from calibration_source import once
from analyze_placement_wmig import analyze,compare,finish,MODE
OLD=IMAGE_DIR/'joint-controlled-end-to-end-v1-20261105-results'
PROGRAM=IMAGE_DIR/'joint-controlled-placement-wmig-build-v2-20261109-results'
MODES=('G2',MODE)


def wrapper(text):
    return once(old_wrapper(text),'( $1 == G0 || $1 == G1 || $1 == G2 )',
        '( $1 == G2 || $1 == G2_WMIG_OFF )')


def wait_for_slot(output=None):
    deadline=time.monotonic()+900
    while True:
        listing=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
        with socket.socket() as sock:occupied=sock.connect_ex(('127.0.0.1',8101))==0
        if str(IMAGE_DIR)+'/' not in listing and not occupied:return
        require(time.monotonic()<deadline,'等待现有任务退出超过15分钟；不强占端口、不关闭其他任务')
        if output is not None:record(output,阶段='排队等待其他任务归档退出，不启动本轮客机')
        time.sleep(5)


def reuse_prepared(output,source):
    source=Path(source).resolve();require(source.parent==IMAGE_DIR,'准备来源越界')
    state=json.loads((source/'状态.json').read_text());require(state.get('当前客机PID') is None,'来源仍有客机')
    info=json.loads((source/'共同材料身份.json').read_text());old=source/'共同材料'
    require(digest(old/'固定输入复用.tar')==info['输入包SHA256'],'已准备输入不同')
    require(digest(old/'程序及工具.tar.gz')==info['程序包SHA256'],'已准备程序包不同')
    require(digest(old/'db_bench')==info['程序SHA256'],'已准备程序不同')
    target=output/'共同材料';target.mkdir()
    for p in old.iterdir():
        if p.is_file():os.link(p,target/p.name)
    stamp(output/'共同材料身份.json',info)
    stamp(output/'准备材料复用.json',dict(来源=str(source),仅复用已核验文件=True,复用业务运行状态=False,不重复占用输入传输空间=True))
    return info


def capture_aux_location(run):
    code="from pathlib import Path;import json,subprocess;r=Path("+repr(run.target)+");p=r/'p1/aux/rrzc-dbbench';data={'目录':str(p),'实际路径':str(p.resolve()),'存在':p.exists(),'fd':'未直接采集，不能编造'}\n"
    code+="for name,cmd in [('挂载',['findmnt','-J','-T',str(p)]),('设备',['lsblk','-J','-o','NAME,TYPE,FSTYPE,MOUNTPOINTS'])]:\n q=subprocess.run(cmd,text=True,capture_output=True);data[name]={'返回':q.returncode,'stdout':q.stdout,'stderr':q.stderr}\n"
    code+="(r/'aux路径归属.json').write_text(json.dumps(data,ensure_ascii=False))"
    run.ssh('python3 -c '+shlex.quote(code),45)

def prepare(output):
    record(output,阶段='核验共用程序及既有输入检查点，不重编/不重新装载')
    ref=replay_source(OLD)
    marker=reserve_checkpoint(IMAGE_DIR,Path(ref['output']).name.removesuffix('-results'),Path(ref['output']))
    atomic_json(marker,dict(output=ref['output'],status='archived',archive=ref['archive'],sha256=ref['sha256']))
    state,_=closed_archive(PROGRAM);program=PROGRAM/state['标签']
    proof=json.loads((program/'集成构建核验.json').read_text())
    require(proof['通过'] and digest(program/'db_bench')==proof['程序SHA256'],'已验收程序身份不同')
    source=output/'共同材料';source.mkdir();old=OLD/'joint-controlled-integrated-pool-v2-20261104'
    shutil.copy2(program/'db_bench',source/'db_bench');shutil.copy2(old/'space_probe',source/'space_probe')
    for name in ('logical_input_guest.py','calibration_guest.py','strict_stats_guest.py','metadata_start_guest.py',
                 'restore_pool_input_guest.py','restore_calibration_guest.py'):
        shutil.copyfile(OLD/'源码'/name,source/name)
    shutil.copyfile(old/'元数据起点目标.json',source/'元数据起点目标.json')
    (source/'run_placement_business_guest.sh').write_text(wrapper((OLD/'源码/run_pool_business_guest.sh').read_text()))
    subprocess.run(['bash','-n',str(source/'run_placement_business_guest.sh')],check=True)
    (source/'受控标签.txt').write_text('')
    # 流式复制原始检查点成员，只生成一份传输包，两组复用；不另存逻辑输入检查点。
    plan=json.loads((OLD/'源码/固定输入复用清单.json').read_text());files={}
    prefix=ref['label']+'/逻辑输入/';payload=source/'固定输入复用.tar'
    with tarfile.open(ref['archive'],'r|gz') as original,tarfile.open(payload,'x') as out:
        for member in original:
            if not member.isfile() or not member.name.startswith(prefix):continue
            relative='逻辑输入/'+member.name[len(prefix):]
            require(relative in plan['文件'] and '..' not in Path(relative).parts,'未知输入成员')
            data=original.extractfile(member).read();sha=hashlib.sha256(data).hexdigest()
            require(sha==plan['文件'][relative],'输入成员身份不同')
            item=tarfile.TarInfo(relative);item.mode=0o644;item.size=len(data);out.addfile(item,io.BytesIO(data));files[relative]=sha
        for relative,sha in plan['文件'].items():
            if not relative.startswith('参考起点/'):continue
            path=old/relative;require(digest(path)==sha,'旧参考起点身份不同')
            out.add(path,arcname=relative,recursive=False);files[relative]=sha
    require(files==plan['文件'],'输入包成员不完整')
    plan.update(文件=files,压缩包SHA256=digest(payload),来源归档SHA256=ref['sha256'])
    stamp(source/'固定输入复用清单.json',plan)
    manifest={p.name:digest(p) for p in source.iterdir() if p.is_file() and p!=payload}
    stamp(source/'部署身份.json',manifest)
    small=source/'程序及工具.tar.gz'
    with tarfile.open(small,'x:gz') as tar:
        for p in source.iterdir():
            if p.is_file() and p not in (payload,small):tar.add(p,arcname=p.name,recursive=False)
    info=dict(固定输入引用=ref,程序SHA256=proof['程序SHA256'],工具SHA256=manifest,
        输入包SHA256=plan['压缩包SHA256'],程序包SHA256=digest(small),
        客机工作目录=plan['客机工作目录'],输入成员=len(files),原逻辑输入=True,
        观测说明='来源只复用输入字节，不复用旧性能；两组统一关闭完整FTL追踪、开启组成记录')
    stamp(output/'共同材料身份.json',info);return info

def run_mode(output,args,password,mode,info,parents):
    folder=output/mode;folder.mkdir();label=args.label+'-'+mode.lower().replace('_','-')
    run=CalibrationRun(SimpleNamespace(label=label),password,folder,'calibration',8101,PARENT,retain_image=False)
    run.target=info['客机工作目录'];run.state.update(标签=Path(run.target).name,宿主实验标签=label,
        GC启用=True,模式=mode,边界='独立客机G2与W迁移准入配对消融；单轮无额外Greedy重复')
    run.fresh_image=True;run.profile=profile8();run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    require(parent_identity()==parents,'父镜像链发生变化')
    stamp(folder/'父链启动身份.json',parents)
    configure(run,ftl_audit=False)
    ref=info['固定输入引用'];run.archive_input_reference=ref
    run.archive_exclusions+=('逻辑输入/*.bin.gz','程序及工具.tar.gz','p1-evidence.tar.gz')
    policy=run.state['结果保存策略'];policy.update(固定输入引用=ref,本轮归档含输入字节=False,
        组成记录=True,来源仅作输入不比较旧性能=True,两组共同程序SHA256=info['程序SHA256'])
    stamp(folder/'结果保存策略.json',policy);run.record(结果保存策略=policy)
    error=None
    try:
        wait_for_slot(output)
        preflight()
        run.start();record(output,阶段=mode+'独立启动并部署',当前模式=mode,当前客机PID=run.state['当前客机PID'])
        run.ssh(f'mkdir {run.target}');run.deployed=True
        source=output/'共同材料'
        run.command(run.scp()+[str(source/'程序及工具.tar.gz'),str(source/'固定输入复用.tar'),f'femu@127.0.0.1:{run.target}/'],300)
        require(run.ssh(f'sha256sum {run.target}/程序及工具.tar.gz').split()[0]==info['程序包SHA256'],'程序包传输不同')
        run.ssh(f'tar -xzf {run.target}/程序及工具.tar.gz -C {run.target}')
        for name,sha in info['工具SHA256'].items():require(run.ssh(f'sha256sum {run.target}/{name}').split()[0]==sha,'工具部署不同')
        run.ssh(f'test -x {run.target}/db_bench && test -x {run.target}/space_probe')
        run.ssh(f'python3 {run.target}/restore_pool_input_guest.py {run.target}',300)
        run.record(阶段=mode+'核验真实8/12和全空起点')
        initial_code="from pathlib import Path;import json,subprocess,sys;sys.path.insert(0,"+repr(run.target)+");from calibration_guest import blocks;from strict_stats_guest import read_stats;r=Path("+repr(run.target)+")\n"
        initial_code+="z=json.loads(subprocess.check_output(['nvme','zns','report-zones','/dev/nvme0n1','-o','json']));i=json.loads(subprocess.check_output(['nvme','zns','id-ns','/dev/nvme0n1','-o','json']));b=blocks();s=read_stats();assert i['mor']+1==8 and i['mar']+1==12;assert len(z['zone_list'])==64 and all(x['state']=='EMPTY' and x['wp']==x['slba'] for x in z['zone_list']);assert all(x['RC']==0 for x in b) and s['阈值触发事件']==0;(r/'新客机初始状态.json').write_text(json.dumps(dict(Zone=z,Identify=i,全部块=b,严格计数=s),ensure_ascii=False))"
        run.root('python3 -c '+shlex.quote(initial_code),60)
        run.root(f'RRZC_ARM_TAG=p1 nohup setsid bash {run.target}/run_placement_business_guest.sh {mode} '
            f'< /dev/null > {run.target}/p1.启动.log 2>&1 & echo $! > {run.target}/p1.pid',45)
        started=changed=time.monotonic();previous=None
        while time.monotonic()-started<3600:
            disk_space();check_baseline_budget(0)
            point=run.ssh(f'if test -f {run.target}/p1/finished; then cat {run.target}/p1/wrapper.exit; else echo 运行中; fi; '
                f'test ! -f {run.target}/p1/phase.txt || cat {run.target}/p1/phase.txt; '
                f'for f in load.log run.log 请求提交.bin 应用完成.bin verify.log; do test ! -f {run.target}/p1/$f || stat -c "%n %s" {run.target}/p1/$f; done; '
                f'test ! -f {run.target}/p1/run.log || tail -1 {run.target}/p1/run.log; true')
            run.record(阶段=mode+'完整业务及收尾',最近观测=point)
            record(output,阶段=mode+'完整业务及收尾',当前模式=mode,当前客机PID=run.state['当前客机PID'],最近观测=point)
            if point!=previous:previous=point;changed=time.monotonic()
            require(time.monotonic()-changed<180,'180秒无真实请求/文件进展')
            if point.splitlines()[0]!='运行中':require(point.splitlines()[0]=='0','业务失败，保留原始结果');break
            time.sleep(10)
        else:raise RuntimeError('单模式超过一小时，采证并关闭')
        capture_aux_location(run)
    except BaseException as e:
        error=e;run.record(原始错误=repr(e))
        if run.deployed:
            try:interrupt(run)
            except Exception as stopped:run.record(业务中断反馈=repr(stopped))
            try:capture_unsealed_logs(run)
            except Exception as captured:run.record(异常维护采证反馈=repr(captured))
        raise
    finally:
        if run.deployed:
            try:normalize_permissions(run)
            except Exception as permission:run.record(采证权限反馈=repr(permission))
        run.finish(error)
        record(output,阶段=mode+'已归档退出',当前客机PID=None)
    run.record(结果收尾=compact(folder))
    local=folder/run.state['标签']/'p1';report=analyze(local,mode);stamp(folder/'业务核验.json',report)
    if mode!='G2':stamp(folder/'同对起点请求核验.json',compare(output/'G2'/run.state['标签']/'p1',local))
    record(output,阶段=mode+'请求与维护账目通过',已完成=[*json.loads((output/'状态.json').read_text()).get('已完成',[]),mode])

def execute(output,args,password):
    info=reuse_prepared(output,args.prepared_source) if args.prepared_source else prepare(output);parents=parent_identity();stamp(output/'父链共同身份.json',parents)
    for mode in MODES:run_mode(output,args,password,mode,info,parents)
    result=finish(output);record(output,阶段='G2与W迁移准入配对完成，均已归档退出',完整实验完成=True,总迁移变化百分比=result['总迁移变化百分比'])
    payload=output/'共同材料/固定输入复用.tar'
    require(digest(Path(info['固定输入引用']['archive']))==info['固定输入引用']['sha256'],'源归档变化，保留传输包')
    stamp(output/'共享传输包回收核验.json',dict(路径=str(payload),SHA256=digest(payload),来源=info['固定输入引用']))
    payload.unlink()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',default='joint-controlled-placement-wmig-pair-v1-20261109')
    p.add_argument('--detach',action='store_true');p.add_argument('--check',action='store_true');p.add_argument('--prepared-source',type=Path);args=p.parse_args()
    require(args.label.startswith('joint-controlled-placement-wmig-pair-') and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.label),'标签非法')
    wait_for_slot()
    resource=preflight();used=check_baseline_budget(8<<30)
    require(resource['data可用字节']>=42<<30 and resource['可用内存字节']>=30<<30,'原业务保护线不足')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖原结果')
    if args.check:print(json.dumps(resource,ensure_ascii=False));return
    # 必须有新门控真实设备及复制映射核验，不仅凭编译成功启动业务。
    proof=json.loads((PROGRAM/'设备集成核验.json').read_text());require(proof['通过'] and proof['模式']==MODE,'构建设备验收未通过')
    password=getpass.getpass('G2配对业务认证（仅内存）：');output.mkdir();freeze=output/'入口源码';freeze.mkdir()
    for path in [Path(__file__),ROOT/'tests/placement_wmig_source.py',ROOT/'tests/run_placement_ablation_host.py',ROOT/'tests/placement_business_source.py',ROOT/'analyze_placement_wmig.py',ROOT/'analyze_placement_business.py',ROOT/'analyze_placement_integration.py',ROOT/'analyze_phase_time.py']:
        shutil.copyfile(path,freeze/path.name)
    stamp(freeze/'SHA256.json',{p.name:digest(p) for p in freeze.iterdir() if p.is_file()})
    stamp(output/'预注册计划.json',dict(模式=list(MODES),平台=profile8(),每组请求=2260992,读=786432,写=1474560,种子=20261023,
        RR=1024,GC启动百分比=20,选择器='两臂Greedy，候选资格不变，Credit/Score只观察',K=1,缓存=False,WAL=False,
        固定预填逻辑值字节=4<<30,队列=4096,同步写批=2048,无QPS限速=True,独立新客机=True,额外Greedy重复=False,
        完整FTL追踪=False,组成记录=True,aux真实调用内存计时=True,auxfd='未直接采集',资源=resource,目录已占字节=used,
        结果边界='单轮配对，含业务/关闭前收尾；不复用旧性能；不将W回退量等同节省；末端空间单列'))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(output,args,password)
    except BaseException as e:record(output,阶段='停止推进并保留失败证据',原始错误=repr(e));raise
    finally:password=None

if __name__=='__main__':main()
