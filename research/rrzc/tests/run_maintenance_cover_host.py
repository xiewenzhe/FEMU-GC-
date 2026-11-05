"""默认关闭的新评分在独立对象上串行验证；每情形先验收Greedy两次再跑模型。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shlex
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
from analyze_colocation_credit import initial_equal
from analyze_maintenance_cover import analyze,arm,CASES
from run_joint_host import digest_manifest


def wrapper():
    text=once(previous_wrapper(),'(smoke|choice)-k[01]-(greedy|model)-r[12]',
        '(smoke|choice|retire|live)-k1-(greedy|model)-r[12]')
    text=once(text,'echo run > "$result/phase.txt"',
        'program=$target/colocation_credit\n'
        'if [[ $scenario == choice || $scenario == smoke ]]; then program=$target/colocation_choice; fi\n'
        'echo run > "$result/phase.txt"')
    text=once(text,'ZENFS_RRZC_UNIT_COST=1',
        'ZENFS_RRZC_MAINTENANCE_COVER=1 ZENFS_RRZC_UNIT_COST=1')
    return once(text,'"$target/colocation_choice" "$scenario"','"$program" "$scenario"')


def valid_tag(tag):
    require(re.fullmatch(r'(smoke|choice|retire|live)-k1-(greedy|model)-r[12]',tag) is not None,'标签非法')


def extract(archive,dest,tag):
    valid_tag(tag);require(not (dest/tag).exists(),'不覆盖逐臂证据')
    with tarfile.open(archive) as tar:
        for m in tar.getmembers():
            p=Path(m.name)
            require(not p.is_absolute() and '..' not in p.parts and p.parts[0]==tag and
                len(p.parts)<=2 and (m.isfile() or (m.isdir() and p.parts==(tag,))),
                '证据越界或包含链接/子目录')
        root=dest/tag;root.mkdir()
        for m in tar.getmembers():
            if m.isdir():continue
            with tar.extractfile(m) as source,(root/Path(m.name).name).open('xb') as out:
                shutil.copyfileobj(source,out)


def collect(run,tag):
    valid_tag(tag);remote=run.target+'/'+tag+'-evidence.tar.gz'
    run.ssh(f"test ! -e {remote} && tar --exclude='{tag}/aux' -czf {remote} -C {run.target} {tag}",120)
    expected=run.ssh('sha256sum '+remote).split()[0]
    dest=run.output/'逐臂采证';dest.mkdir(exist_ok=True);path=dest/(tag+'.tar.gz')
    run.command(run.scp()+[f'femu@127.0.0.1:{remote}',str(path)],180)
    require(digest(path)==expected,'证据传输哈希不一致');extract(path,dest,tag);return dest/tag


def freeze_reader(output):
    target=output/'源码/宿主评分核';target.mkdir()
    for p in (ROOT/'model').glob('*.h'):shutil.copyfile(p,target/p.name)
    shutil.copyfile(ROOT/'tests/maintenance_cover_replay.cc',target/'maintenance_cover_replay.cc')
    binary=target/'维护核重算';dependencies=target/'依赖.d'
    subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread',
        '-MMD','-MF',str(dependencies),'-I'+str(target),str(target/'maintenance_cover_replay.cc'),
        '-o',str(binary)],check=True)
    tokens=shlex.split(dependencies.read_text().split(':',1)[1].replace('\\\n',' '))
    headers={Path(p).name:digest(Path(p)) for p in tokens if p.endswith('.h')}
    require(headers and all(Path(p).parent==target for p in tokens if p.endswith('.h')),'依赖越界')
    stamp(target/'编译身份.json',dict(二进制SHA256=digest(binary),头文件=headers,
        客户端源码SHA256=digest(target/'maintenance_cover_replay.cc')))
    return binary,headers


def execute(args,password,output,binary,headers):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    run.state.update(边界='同空间目标新核真实路径验证，非性能实验',GC启用=True)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+
            [f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='恢复原软件，独立编译新核及两个既有客户端')
        run.ssh(f'python3 {run.target}/restore_calibration_guest.py {run.target}',180)
        run.ssh(f'python3 {run.target}/build_colocation_guest.py {run.target}',900)
        fs=run.target+'/协同池副本/plugin/zenfs/fs/'
        remote=digest_manifest(run.ssh('sha256sum '+' '.join(shlex.quote(fs+n) for n in headers)))
        actual={Path(k).name:v for k,v in remote.items()}
        require(actual==headers,'设备与宿主评分核的实际依赖字节不同')
        stamp(output/'设备宿主核身份核验.json',dict(通过=True,实际头文件=actual,宿主重算SHA256=digest(binary)))
        tag='smoke-k1-greedy-r1';run.record(阶段=tag)
        wait_stage(run,tag,'run_maintenance_cover_guest.sh',180,mode='greedy')
        p=collect(run,tag);require('多候选布局小测通过' in (p/'run.log').read_text(),'布局小测失败')
        for scenario in CASES:
            start=None;reference=None
            for mode in ('greedy','model'):
                paths=[]
                for n in (1,2):
                    tag=f'{scenario}-k1-{mode}-r{n}';run.record(阶段=tag)
                    wait_stage(run,tag,'run_maintenance_cover_guest.sh',600,mode='greedy')
                    p=collect(run,tag);proof=arm(p,scenario,mode,binary)
                    stamp(output/(tag+'-核验.json'),proof);paths.append(p)
                    req=logical_requests(p)
                    if reference is None:reference=req
                    require(req==reference,'同情形请求流不同，停止')
                    run.record(已完成=[*run.state.get('已完成',[]),tag])
                repeated=compare_repeats(*paths);stamp(output/(f'{scenario}-{mode}-重复.json'),repeated)
                require(repeated['通过'],'同配置重复未通过，禁止继续收益比较')
                if start is None:
                    start=paths[0];run.record(本情形Greedy重复通过=scenario)
                else:require(initial_equal(start,paths[0]),'模型与本轮Greedy起点不符')
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
    proof=analyze(output,binary)
    run.record(阶段='三个情形真实对照完成，已归档并关闭客机',对照=proof['对照'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='joint-controlled-maintenance-cover-v1-20261029')
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法')
    admission(BASELINE)
    previous=IMAGE_DIR/'joint-controlled-colocation-credit-v2-20261029-results'
    require(json.loads((previous/'抵扣校准核验.json').read_text())['通过'],'前轮校准未验收')
    state=json.loads((previous/'状态.json').read_text());archive=json.loads((previous/'阶段归档.json').read_text())
    require(state['当前客机PID'] is None and archive['完整'] and archive['业务成功'],'前轮未归档退出')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'前轮归档哈希不符')
    budget=preflight();check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'资源不足')
    budget.update(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30,
        说明='单台串行；独立完整ZenFS对象和归档；原200GiB项目上限及服务器保护线不变')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧实验')
    subprocess.run(['bash','-n'],input=wrapper(),text=True,check=True)
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('新核设备验证认证（仅内存）：');output.mkdir()
    freeze(output,restore_fix=True,reuse_build=PREVIOUS)
    reused=json.loads((BASELINE/'源码/复用诊断客户端.json').read_text())
    freeze_bundle(output,PREVIOUS,DONOR,reused,metadata_reference=False)
    for name in ('restore_calibration_guest.py','build_colocation_guest.py','colocation_source.py',
        'colocation_device.cc','colocation_cases_device.cc','colocation_credit_device.cc','colocation_choice_device.cc',
        'colocation_choice_source.py','unit_cost_source.py','matched_observation_source.py','maintenance_cover_source.py',
        'build_all_blocks_guest.py','calibration_source.py','controlled_gc_source.py','run_maintenance_cover_host.py'):
        shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    for name in ('rrzc_colocation_pool.h','rrzc_colocation_scope.h','rrzc_colocation_zenfs.inc',
                 'rrzc_unit_cost_selector.h','rrzc_maintenance_cover.h'):
        shutil.copyfile(ROOT/'model'/name,output/'源码'/name)
    for name in ('analyze_maintenance_cover.py','analyze_colocation_credit.py','analyze_colocation_choice.py',
                 'analyze_colocation_cases.py','audit_colocation_transactions.py','replay_maintenance_cover.py'):
        shutil.copyfile(ROOT/name,output/'源码'/name)
    (output/'源码/启用协同池四组合').write_text('复用原候选及两个已验证客户端\n')
    (output/'源码/启用同空间目标接线').write_text('仅独立副本；新核默认关闭，原核保留\n')
    (output/'源码/受控标签.txt').write_text('/joint/H.sst H\n/joint/W.sst W\n')
    (output/'源码/run_maintenance_cover_guest.sh').write_text(wrapper())
    binary,headers=freeze_reader(output)
    stamp(output/'预注册计划.json',dict(目的='验证新核空间计价与真实维护链，不预设正收益',平台=profile8(),
        情形=list(CASES),每情形顺序=['greedy-r1','greedy-r2','model-r1','model-r2'],
        客户端='复用四组合与抵扣校准原源码；K1，原候选，严格RR1024，原GC20%',
        检查='共同受控检查门改变RR检查时点，不报告正常并发性能',
        核验='同配置Greedy重复准入、相同起点和请求、实际逐笔复制映射Reset闭合、新核日志独立重算',
        安全='两臂资源和观测相同；不指定victim、不手设RC，不放宽保护预算',
        资源=dict(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30),
        新核头文件=headers,宿主重算SHA256=digest(binary)))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,binary,headers)
    finally:password=None


if __name__=='__main__':main()
