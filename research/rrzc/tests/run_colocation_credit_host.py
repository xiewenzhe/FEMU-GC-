"""单客机串行校准近期RR抵扣；冻结选择器，两类D寿命分别独立重复。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
from run_colocation_host import (admission,BASELINE,DONOR,BINARY,CalibrationRun,PREVIOUS,PARENT,ROOT,
    IMAGE_DIR,check_baseline_budget,detach_supervisor,freeze,freeze_bundle,preflight,profile8,
    stamp,wait_stage,stop_business,preserve_unverified_image,digest,require)
from run_colocation_choice_host import wrapper as previous_wrapper
from calibration_source import once
from analyze_colocation_cases import compare_repeats,logical_requests
from analyze_colocation_credit import analyze,arm,initial_equal,CASES


def wrapper():
    text=once(previous_wrapper(),'(smoke|choice)-k[01]-(greedy|model)-r[12]',
        '(smoke|retire|live)-k1-(greedy|model)-r[12]')
    return once(text,'"$target/colocation_choice"','"$target/colocation_credit"')


def valid_tag(tag):
    require(re.fullmatch(r'(smoke|retire|live)-k1-(greedy|model)-r[12]',tag) is not None,'校准标签非法')


def extract(archive,dest,tag):
    valid_tag(tag);require(not (dest/tag).exists(),'不覆盖已有采证')
    with tarfile.open(archive) as tar:
        for m in tar.getmembers():
            p=Path(m.name)
            require(not p.is_absolute() and '..' not in p.parts and p.parts[0]==tag and
                len(p.parts)<=2 and (m.isfile() or (m.isdir() and p.parts==(tag,))),
                '证据含链接、子目录或越界成员')
        root=dest/tag;root.mkdir()
        for m in tar.getmembers():
            if m.isdir():continue
            with tar.extractfile(m) as source,(root/Path(m.name).name).open('xb') as out:
                shutil.copyfileobj(source,out)


def collect(run,tag):
    valid_tag(tag);remote=run.target+'/'+tag+'-evidence.tar.gz'
    run.ssh(f"test ! -e {remote} && tar --exclude='{tag}/aux' -czf {remote} -C {run.target} {tag}",120)
    expected=run.ssh('sha256sum '+remote).split()[0];dest=run.output/'逐臂采证';dest.mkdir(exist_ok=True)
    path=dest/(tag+'.tar.gz');run.command(run.scp()+[f'femu@127.0.0.1:{remote}',str(path)],180)
    require(digest(path)==expected,'证据传输哈希不一致');extract(path,dest,tag);return dest/tag


def execute(args,password,output):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    run.state.update(边界='相同协同布局下D寿命及后续空间压力的RR抵扣校准；非性能结论',GC启用=True)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+
            [f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='恢复软件，编译原冻结模型与校准载荷')
        run.ssh(f'python3 {run.target}/restore_calibration_guest.py {run.target}',180)
        run.ssh(f'python3 {run.target}/build_colocation_guest.py {run.target}',900)
        tag='smoke-k1-greedy-r1';run.record(阶段=tag)
        wait_stage(run,tag,'run_colocation_credit_guest.sh',180,mode='greedy')
        p=collect(run,tag);require('抵扣校准布局小测通过' in (p/'run.log').read_text(),'布局小测失败')
        for scenario in CASES:
            reference=None;start=None
            for mode in ('greedy','model'):
                paths=[]
                for n in (1,2):
                    tag=f'{scenario}-k1-{mode}-r{n}';run.record(阶段=tag)
                    wait_stage(run,tag,'run_colocation_credit_guest.sh',600,mode='greedy')
                    p=collect(run,tag);stamp(output/(tag+'-核验.json'),arm(p,scenario,mode));paths.append(p)
                    req=logical_requests(p)
                    if reference is None:reference=req
                    require(reference==req,'同情形逻辑输入不同，停止')
                    run.record(已完成=[*run.state.get('已完成',[]),tag])
                repeat=compare_repeats(*paths);stamp(output/(f'{scenario}-{mode}-重复.json'),repeat)
                require(repeat['通过'],'同配置重复未通过，停止并保留首差')
                if start is None:start=paths[0]
                else:require(initial_equal(start,paths[0]),'选择器对照起点不同，停止')
    except BaseException as failed:
        error=failed;run.record(原始错误=repr(failed))
        if run.deployed:
            try:stop_business(run)
            except Exception as stopped:run.record(业务中断反馈=repr(stopped))
        raise
    finally:
        try:run.finish(error)
        except Exception as cleanup:
            retained=preserve_unverified_image(run,cleanup)
            if error is None and not retained:raise
            if error is not None:run.record(收尾另有错误=repr(cleanup))
    proof=analyze(output);run.record(阶段='两类抵扣校准完成，已归档并关闭客机',对照=proof['对照'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='joint-controlled-colocation-credit-v2-20261029')
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法')
    admission(BASELINE)
    previous=IMAGE_DIR/'joint-controlled-colocation-choice-v3-20261029-results'
    require(json.loads((previous/'四组合核验.json').read_text())['通过'],'四组合尚未验收')
    state=json.loads((previous/'状态.json').read_text());arch=json.loads((previous/'阶段归档.json').read_text())
    require(state['当前客机PID'] is None and arch['完整'] and arch['业务成功'],'上一轮未完成归档收尾')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'上一轮归档哈希不符')
    budget=preflight();check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'资源不足')
    budget.update(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30,
        说明='独立完整ZenFS对象及证据归档；单台串行，原项目和服务器保护线不变')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧实验')
    subprocess.run(['bash','-n'],input=wrapper(),text=True,check=True)
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('RR抵扣校准认证（仅内存）：');output.mkdir()
    freeze(output,restore_fix=True,reuse_build=PREVIOUS)
    reused=json.loads((BASELINE/'源码/复用诊断客户端.json').read_text())
    freeze_bundle(output,PREVIOUS,DONOR,reused,metadata_reference=False)
    for name in ('restore_calibration_guest.py','build_colocation_guest.py','colocation_source.py',
        'colocation_device.cc','colocation_cases_device.cc','colocation_credit_device.cc',
        'colocation_choice_source.py','unit_cost_source.py','matched_observation_source.py',
        'build_all_blocks_guest.py','calibration_source.py','controlled_gc_source.py',
        'run_colocation_credit_host.py','run_colocation_choice_host.py','run_colocation_host.py','greedy_admission_archive.py'):
        shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    for name in ('rrzc_colocation_pool.h','rrzc_colocation_scope.h','rrzc_colocation_zenfs.inc','rrzc_unit_cost_selector.h'):
        shutil.copyfile(ROOT/'model'/name,output/'源码'/name)
    for name in ('analyze_colocation_credit.py','analyze_colocation_choice.py','analyze_colocation_cases.py','audit_colocation_transactions.py'):
        shutil.copyfile(ROOT/name,output/'源码'/name)
    (output/'源码/启用协同池抵扣校准').write_text('K1、original、冻结选择器；未来D寿命不传给模型\n')
    (output/'源码/受控标签.txt').write_text('/joint/H.sst H\n/joint/W.sst W\n')
    (output/'源码/run_colocation_credit_guest.sh').write_text(wrapper())
    stamp(output/'预注册计划.json',dict(目的='验证真实少复制与维护推迟，原模型冻结，不预设正收益',
        平台=profile8(),候选='原资格，K1相同布局；不指定victim、不设置RC',
        源布局='W126/H2同区，D1.5/U126.5同区；47x128MiB填充文件',
        准备='W/U先退役，等待31秒；起点有效6019.5MiB、空闲1536MiB、满区无效252.5MiB',
        业务='9段100读和1秒历史等待；检查、312读、检查；D退役或保持；256MiB写；检查；1024读；检查',
        两情形区别='只改变第三阶段D是否删除；模型输入标签不含D未来寿命',
        重复=2,完整内容校验=True,共同受控检查改变RR检查时点=True,
        成功判据='同起点同请求同配置重复、候选及逐笔复制提交Reset闭合；不以正收益作为准入',
        终态='报告有效量、空闲、满区可回收、映射及RC；不将少回收空间当净收益',
        资源=dict(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30,保护门槛不变=True)))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output)
    finally:password=None


if __name__=='__main__':main()
