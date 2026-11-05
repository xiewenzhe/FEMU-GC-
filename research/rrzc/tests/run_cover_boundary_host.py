"""冻结已验收ZenFS对象，串行运行两压力三寿命；每配置先做Greedy重复准入。"""
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
from analyze_cover_boundary import analyze,arm,CASES
from run_joint_host import digest_manifest


def wrapper():
    text=once(previous_wrapper(),'(smoke|choice)-k[01]-(greedy|model)-r[12]',
        '(smoke|(low|high)-(early|late|live))-k1-(greedy|model)-r[12]')
    text=once(text,'ZENFS_RRZC_UNIT_COST=1',
        'ZENFS_RRZC_MAINTENANCE_COVER=1 ZENFS_RRZC_UNIT_COST=1')
    return once(text,'"$target/colocation_choice" "$scenario"','"$target/colocation_credit" "$scenario"')


def valid_tag(tag):
    require(re.fullmatch(r'(smoke|(low|high)-(early|late|live))-k1-(greedy|model)-r[12]',tag) is not None,'标签非法')


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



MODEL_PREVIOUS=IMAGE_DIR/'joint-controlled-maintenance-cover-v1-20261029-results'


def freeze_object(output,previous):
    guest=previous/previous.name.removesuffix('-results')
    identity=json.loads((guest/'协同池构建身份.json').read_text())
    obj=guest/'fs_colocation.o';require(digest(obj)==identity['对象SHA256'],'旧对象身份不同')
    files={'fs_colocation.o':obj}
    for p in (guest/'协同池副本').rglob('*'):
        if p.is_file():
            require(not p.is_symlink() and p.suffix in ('.cc','.h','.inc'),'冻结树含未确认文件')
            files[str(p.relative_to(guest))]=p
    manifest={n:digest(p) for n,p in files.items()};payload=output/'源码/冻结对象.tar.gz'
    with tarfile.open(payload,'x:gz') as tar:
        for member,path in files.items():tar.add(path,arcname=member,recursive=False)
    banned=[p.stem+'.o' for p in (guest/'协同池副本/plugin/zenfs/fs').glob('*.cc')]
    stamp(output/'源码/冻结对象清单.json',dict(来源=str(previous),对象SHA256=identity['对象SHA256'],
        原库SHA256=identity['原库SHA256'],文件=manifest,压缩包SHA256=digest(payload),禁止旧对象=banned))
    old=previous/'源码/宿主评分核';new=output/'源码/宿主评分核';shutil.copytree(old,new)
    record=json.loads((new/'编译身份.json').read_text());headers=record['头文件'];binary=new/'维护核重算'
    require(digest(binary)==record['二进制SHA256'],'宿主重算器身份不同')
    for name,sha in headers.items():
        require(digest(new/name)==sha==digest(guest/'协同池副本/plugin/zenfs/fs'/name),
                '冻结宿主与已部署头文件不同：'+name)
        require(digest(ROOT/'model'/name)==sha,'工作区模型已变化，禁止混入边界轮：'+name)
    return binary,headers


def execute(args,password,output,binary,headers):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    run.state.update(边界='固定候选的两压力三寿命边界，非性能实验',GC启用=True)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+
            [f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='恢复原软件及冻结协同对象，仅编译边界客户端')
        run.ssh(f'python3 {run.target}/restore_calibration_guest.py {run.target}',180)
        run.ssh(f'python3 {run.target}/build_cover_boundary_guest.py {run.target}',900)
        fs=run.target+'/协同池副本/plugin/zenfs/fs/'
        remote=digest_manifest(run.ssh('sha256sum '+' '.join(shlex.quote(fs+n) for n in headers)))
        actual={Path(k).name:v for k,v in remote.items()}
        require(actual==headers,'设备与宿主评分核的实际依赖字节不同')
        stamp(output/'设备宿主核身份核验.json',dict(通过=True,实际头文件=actual,宿主重算SHA256=digest(binary)))
        tag='smoke-k1-greedy-r1';run.record(阶段=tag)
        wait_stage(run,tag,'run_cover_boundary_guest.sh',180,mode='greedy')
        p=collect(run,tag);require('边界布局小测通过' in (p/'run.log').read_text(),'布局小测失败')
        for scenario in CASES:
            start=None;reference=None
            for mode in ('greedy','model'):
                paths=[]
                for n in (1,2):
                    tag=f'{scenario}-k1-{mode}-r{n}';run.record(阶段=tag)
                    wait_stage(run,tag,'run_cover_boundary_guest.sh',600,mode='greedy')
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
    run.record(阶段='六个边界配置完成，已归档并关闭客机',对照=proof['对照'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='joint-controlled-cover-boundary-v1-20261029')
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法')
    admission(BASELINE)
    previous=IMAGE_DIR/'joint-controlled-maintenance-cover-v1-20261029-results'
    require(json.loads((previous/'同空间目标核验.json').read_text())['通过'],'前轮校准未验收')
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
    password=getpass.getpass('边界实验认证（仅内存）：');output.mkdir()
    freeze(output,restore_fix=True,reuse_build=PREVIOUS)
    reused=json.loads((BASELINE/'源码/复用诊断客户端.json').read_text())
    freeze_bundle(output,PREVIOUS,DONOR,reused,metadata_reference=False)
    for name in ('restore_calibration_guest.py','build_cover_boundary_guest.py','build_all_blocks_guest.py',
                 'cover_boundary_device.cc','run_cover_boundary_host.py'):
        shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    for name in ('colocation_device.cc','colocation_cases_device.cc'):
        shutil.copyfile(previous/'源码'/name,output/'源码'/name)
    for name in ('analyze_cover_boundary.py','analyze_maintenance_cover.py','analyze_colocation_cases.py',
                 'audit_colocation_transactions.py','replay_maintenance_cover.py'):
        shutil.copyfile(ROOT/name,output/'源码'/name)
    (output/'源码/受控标签.txt').write_text('/joint/H.sst H\n/joint/W.sst W\n')
    (output/'源码/run_cover_boundary_guest.sh').write_text(wrapper())
    binary,headers=freeze_object(output,previous)
    stamp(output/'预注册计划.json',dict(目的='隔离空间压力与未来失效时点，不改模型，不预设正收益',平台=profile8(),
        情形=list(CASES),每情形顺序=['greedy-r1','greedy-r2','model-r1','model-r2'],
        固定候选='H2MiB/D1.5MiB，W126/U126.5MiB先退役，首轮H真实读取900次、D为0',
        空间='low填充47区、空闲1536MiB；high填充51区、空闲1024MiB；原候选资格不变',
        寿命='early在首轮选择后失效；late在后续256MiB写入及第三检查后失效；live持续存活',
        读取='900+312+1024次真实热读；原31秒历史等待和分段采样节奏；不报告应用吞吐',
        核验='先同配置Greedy两次准入；每臂独立Reset/mkfs、同请求、同候选及逐笔复制闭合',
        范围='受控ZenFS文件，不是RocksDB表或公开负载；未来D不提供给选择器',
        资源=dict(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30),
        冻结头文件=headers,宿主重算SHA256=digest(binary)))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,binary,headers)
    finally:password=None


if __name__=='__main__':main()
