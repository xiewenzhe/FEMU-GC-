"""完整三模式业务消融：共用冻结输入/程序，每模式独立新客机，串行归档回收。"""
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
import subprocess
import tarfile
import time
from types import SimpleNamespace
from run_calibration_host import CalibrationRun,detach_supervisor,check_baseline_budget
from run_all_blocks_followup_host import PARENT
from run_pool_dbbench_host import BINARY
from run_rr_path_host import preflight,ROOT,IMAGE_DIR
from run_ycsb_quick_host import profile8,disk_space
from run_dbbench_parallel_host import stamp
from result_storage import configure,compact,replay_source,reserve_checkpoint,atomic_json,closed_archive
from placement_business_source import wrapper
from analyze_placement_business import analyze,compare,finish
from ycsb_af import digest,require

OLD=IMAGE_DIR/'joint-controlled-end-to-end-v1-20261105-results'
PROGRAM=IMAGE_DIR/'joint-controlled-placement-exit-fix-v2-20261106-results'
MODES=('G0','G1','G2')


def record(output,**values):
    path=output/'状态.json';state=json.loads(path.read_text()) if path.exists() else {}
    state.update(values,监督进程PID=os.getpid(),更新时间UTC=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
    stamp(path,state);print(state.get('阶段','状态已更新'),flush=True)


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
    # 流式复制原始检查点成员，只生成一份传输包，三组复用；不另存逻辑输入检查点。
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
        观测说明='来源只复用输入字节，不复用旧性能；三组统一关闭完整FTL追踪、开启组成记录')
    stamp(output/'共同材料身份.json',info);return info


def parent_identity():
    rows=json.loads(subprocess.check_output([str(ROOT.parents[1]/'build-femu/qemu-img'),
        'info','--backing-chain','--output=json',str(PARENT)],text=True))
    return [dict(路径=r['filename'],SHA256=digest(Path(r['filename']))) for r in rows]


def normalize_permissions(run):
    code="from pathlib import Path;import os,json,hashlib;r=Path("+repr(run.target)+");o=r.stat();rows=[]\n"
    code+="for p in r.rglob('*.bin'):\n if p.is_file() and not p.is_symlink():\n  h=hashlib.sha256(p.read_bytes()).hexdigest();os.chown(p,o.st_uid,o.st_gid);assert hashlib.sha256(p.read_bytes()).hexdigest()==h;rows.append([str(p.relative_to(r)),h])\n"
    code+="(r/'采证权限核验.json').write_text(json.dumps(rows,ensure_ascii=False))"
    run.root('python3 -c '+shlex.quote(code),90)


def capture_unsealed_logs(run):
    # 失败时包装器可能尚未执行seal_log；保留本轮尚未封存的/tmp维护原始证据。
    code="from pathlib import Path;import os,json;r=Path("+repr(run.target)+");rows=[]\n"
    code+="for before in r.glob('p1/*.logs.before.txt'):\n phase=before.name.removesuffix('.logs.before.txt');old=set(before.read_text().splitlines());dest=before.parent/(phase+'.zenfs.log')\n if not dest.exists():\n  logs=[p for p in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(p) not in old]\n  for p in logs:\n   target=before.parent/('failed.'+phase+'.'+p.name)\n   if not target.exists():os.link(p,target);rows.append(str(target))\n"
    code+="(r/'异常维护日志采证.json').write_text(json.dumps(rows,ensure_ascii=False))"
    run.root('python3 -c '+shlex.quote(code),45)


def interrupt(run):
    code="from pathlib import Path;import os,signal,time;r=Path("+repr(run.target)+")\n"
    code+="p=r/'p1/bench.pid'\nif p.is_file():\n pid=int(p.read_text());proc=Path('/proc')/str(pid)\n if proc.exists():\n  assert (proc/'exe').resolve()==r/'db_bench';os.kill(pid,signal.SIGINT)\n"
    run.root('python3 -c '+shlex.quote(code),45)
    # 等包装器完成seal/trap后再采证，避免归档仍在修改的文件。
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        done=run.ssh(f'test ! -d {run.target}/p1 || test -f {run.target}/p1/finished; echo $?').strip()
        if done=='0':return
        time.sleep(1)
    raise RuntimeError('业务中断后包装器未退出，保留退出证据后正常关机')


def run_mode(output,args,password,mode,info,parents):
    folder=output/mode;folder.mkdir();label=args.label+'-'+mode.lower()
    run=CalibrationRun(SimpleNamespace(label=label),password,folder,'calibration',8101,PARENT,retain_image=False)
    run.target=info['客机工作目录'];run.state.update(标签=Path(run.target).name,宿主实验标签=label,
        GC启用=True,模式=mode,边界='独立客机完整三模式消融；单轮无额外Greedy重复')
    run.fresh_image=True;run.profile=profile8();run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    require(parent_identity()==parents,'父镜像链发生变化')
    stamp(folder/'父链启动身份.json',parents)
    configure(run,ftl_audit=False)
    ref=info['固定输入引用'];run.archive_input_reference=ref
    run.archive_exclusions+=('逻辑输入/*.bin.gz','程序及工具.tar.gz','p1-evidence.tar.gz')
    policy=run.state['结果保存策略'];policy.update(固定输入引用=ref,本轮归档含输入字节=False,
        组成记录=True,来源仅作输入不比较旧性能=True,三组共同程序SHA256=info['程序SHA256'])
    stamp(folder/'结果保存策略.json',policy);run.record(结果保存策略=policy)
    error=None
    try:
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
    if mode!='G0':stamp(folder/'同对起点请求核验.json',compare(output/'G0'/run.state['标签']/'p1',local))
    record(output,阶段=mode+'请求与维护账目通过',已完成=[*json.loads((output/'状态.json').read_text()).get('已完成',[]),mode])


def execute(output,args,password):
    info=prepare(output);parents=parent_identity();stamp(output/'父链共同身份.json',parents)
    for mode in MODES:run_mode(output,args,password,mode,info,parents)
    result=finish(output);record(output,阶段='三模式完整业务消融完成，均已归档退出',完整实验完成=True,差异=result['差异'])
    payload=output/'共同材料/固定输入复用.tar'
    require(digest(Path(info['固定输入引用']['archive']))==info['固定输入引用']['sha256'],'源归档变化，保留传输包')
    stamp(output/'共享传输包回收核验.json',dict(路径=str(payload),SHA256=digest(payload),来源=info['固定输入引用']))
    payload.unlink()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',default='joint-controlled-placement-ablation-v1-20261106')
    p.add_argument('--detach',action='store_true');p.add_argument('--check',action='store_true');args=p.parse_args()
    require(args.label.startswith('joint-controlled-placement-ablation-') and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.label),'标签非法')
    budget=preflight();used=check_baseline_budget(8<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'原完整业务保护线不足')
    budget.update(新增磁盘预算字节=8<<30,目录已占字节=used,说明='复用程序和同一输入传输包，不编译静态库；串行单客机')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖已有结果')
    if args.check:print(json.dumps(budget,ensure_ascii=False));return
    password=getpass.getpass('三模式完整业务认证（仅内存）：');output.mkdir()
    freeze=output/'入口源码';freeze.mkdir()
    for path in (Path(__file__),ROOT/'tests/placement_business_source.py',ROOT/'analyze_placement_business.py',
                 ROOT/'analyze_placement_integration.py'):
        shutil.copyfile(path,freeze/path.name)
    stamp(freeze/'SHA256.json',{p.name:digest(p) for p in freeze.iterdir() if p.is_file()})
    stamp(output/'预注册计划.json',dict(模式=list(MODES),平台=profile8(),每组请求=2260992,
        读=786432,写=1474560,种子=20261023,中性预填逻辑值字节=4<<30,
        RR=1024,GC启动百分比=20,选区='三组Greedy，原模型只观察',原候选资格=True,
        缓存=False,WAL=False,应用队列=4096,写完成批大小=2048,无QPS限速=True,
        独立新客机=True,额外Greedy重复=False,完整设备追踪=False,组成记录=True,
        旧输入观测不同='来源只复用字节；本轮三组观测共同一致，不使用旧性能',资源=budget,
        结果口径='业务及关闭前收尾；verify阶段单列；单轮不宣称统计显著性或所有未来RR'))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(output,args,password)
    except BaseException as error:record(output,阶段='停止推进，保留原始失败证据',原始错误=repr(error));raise
    finally:password=None


if __name__=='__main__':main()
