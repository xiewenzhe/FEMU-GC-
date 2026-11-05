"""真实db_bench筛查：固定K1与选择器、准备期显式标签、Greedy重复后才允许模型。"""
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
from pool_dbbench_source import source as source_wrapper,business as business_wrapper
from pool_dbbench_guest import roles,table
from pool_dbbench_sync_source import require_ready,patched as sync_patch
from calibration_source import once
from analyze_colocation_cases import compare_repeats,logical_requests
from analyze_colocation_credit import initial_equal
from analyze_pool_dbbench import analyze,arm,repeat_guard,SEMANTIC,space_handoff
from analyze_reproducibility import initial,request_comparison
from run_joint_host import digest_manifest



def valid_tag(tag):
    require(re.fullmatch(r'(source|p1|p2|m1|m2)',tag) is not None,'标签非法')


def extract(archive,dest,tag):
    valid_tag(tag);require(not (dest/tag).exists(),'不覆盖逐臂证据')
    with tarfile.open(archive) as tar:
        members=tar.getmembers()
        require(len({m.name for m in members})==len(members),'归档成员重复')
        for m in members:
            p=Path(m.name)
            require(not p.is_absolute() and '..' not in p.parts and p.parts[0]==tag and
                (m.isfile() or m.isdir()),'证据越界或包含链接/特殊文件')
        root=dest/tag;root.mkdir()
        for m in members:
            if m.isdir():continue
            target=dest/m.name;target.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as source,target.open('xb') as out:
                shutil.copyfileobj(source,out)


def collect(run,tag):
    valid_tag(tag);remote=run.target+'/'+tag+'-evidence.tar.gz'
    run.ssh(f"test ! -e {remote} && tar --exclude='{tag}/aux' -czf {remote} -C {run.target} {tag}",120)
    expected=run.ssh('sha256sum '+remote).split()[0]
    dest=run.output/'逐臂采证';dest.mkdir(exist_ok=True);path=dest/(tag+'.tar.gz')
    run.command(run.scp()+[f'femu@127.0.0.1:{remote}',str(path)],180)
    require(digest(path)==expected,'证据传输哈希不一致');extract(path,dest,tag);return dest/tag



MODEL_PREVIOUS=IMAGE_DIR/'joint-controlled-maintenance-cover-v1-20261029-results'
VERIFIED_DB=IMAGE_DIR/'joint-controlled-pool-dbbench-v4-20261029-results'


def freeze_object(output,previous):
    guest=previous/previous.name.removesuffix('-results')
    legacy=guest/'协同池构建身份.json'
    if legacy.is_file():identity=json.loads(legacy.read_text())
    else:
        build=json.loads((guest/'真实DB构建核验.json').read_text())
        require(build['通过'],'真实DB构建尚未验收')
        identity=dict(对象SHA256=build['冻结ZenFS对象SHA256'],原库SHA256=build['原库SHA256'])
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


def verified_database(previous):
    """收益对照只能复用已完整归档且通过同步及Greedy重复验收的程序。"""
    state=json.loads((previous/'状态.json').read_text());archive=json.loads((previous/'阶段归档.json').read_text())
    audit=json.loads((previous/'同步补接验收.json').read_text())
    repeat=json.loads((previous/'Greedy重复核验.json').read_text())
    require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and
        state['当前客机PID'] is None and audit['通过'] and repeat['通过'],'已验收真实DB未完整归档退出')
    require(digest(Path(state['镜像处理']['archive']))==audit['归档SHA256']==state['镜像处理']['archive_sha256'],
            '真实DB来源归档哈希不同')
    guest=previous/state['标签'];build=json.loads((guest/'真实DB构建核验.json').read_text())
    sync=json.loads((guest/'同步接线构建核验.json').read_text())
    require(build['通过'] and sync['通过'] and sync['真实线程协议自检通过'] and
        digest(guest/'fs_colocation.o')==build['冻结ZenFS对象SHA256']==sync['新对象SHA256'] and
        digest(guest/'db_bench')==build['db_benchSHA256'],'已验收程序或对象字节不同')
    fs=guest/'协同池副本/plugin/zenfs/fs'
    require_ready((fs/'fs_zenfs.cc').read_text(),(fs/'zbd_zenfs.cc').read_text())
    return guest,build


def freeze_verified_programs(output,previous,guest,build):
    programs={}
    for name in ('db_bench','space_probe'):
        programs[name]=digest(guest/name);shutil.copyfile(guest/name,output/'源码'/(name+'.frozen'))
    shutil.copyfile(guest/'同步接线构建核验.json',output/'源码/同步接线构建核验.json')
    stamp(output/'源码/复用真实DB程序.json',dict(来源=str(previous),程序=programs,
        对象SHA256=build['冻结ZenFS对象SHA256'],原库SHA256=build['原库SHA256'],
        说明='已验收同步对象与真实程序逐字复用；本轮仍重新构造起点及重复Greedy，不借用旧维护结果'))


def freeze_sync_components(output):
    """复用已归档的逐编译单元对象，不复用缺少协议的最终程序。"""
    prior=IMAGE_DIR/'joint-controlled-pool-dbbench-v2-20261029-results'
    state=json.loads((prior/'状态.json').read_text());archive=json.loads((prior/'阶段归档.json').read_text())
    require(archive['完整'] and not archive['客机仍存活'] and state['当前客机PID'] is None,'客户端来源未归档退出')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'客户端来源归档不同')
    old=prior/state['标签'];build=json.loads((old/'真实DB构建核验.json').read_text())
    require(build['通过'] and digest(old/'db_bench')==build['db_benchSHA256'],'原客户端构建身份不同')
    pool=MODEL_PREVIOUS/MODEL_PREVIOUS.name.removesuffix('-results')
    require(digest(pool/'fs_colocation.o')==build['冻结ZenFS对象SHA256'],'前端与协同ABI来源不同')
    components={'复用ZenFS对象/'+p.name:p for p in (pool/'协同池对象').glob('*.o')}
    for name in ('客户端.o','诊断-flush_job.cc.o','诊断-sync_point.cc.o','诊断-sync_point_impl.cc.o'):
        components['复用客户端对象/'+name]=old/name
    payload=output/'源码/同步重编对象.tar.gz'
    with tarfile.open(payload,'x:gz') as tar:
        for name,path in sorted(components.items()):
            require(path.is_file() and not path.is_symlink(),'组件不是普通文件')
            tar.add(path,arcname=name,recursive=False)
    stamp(output/'源码/同步重编清单.json',dict(文件={n:digest(p) for n,p in components.items()},
        压缩包SHA256=digest(payload),原对象SHA256=build['冻结ZenFS对象SHA256'],原库SHA256=build['原库SHA256'],
        ZenFS对象=sorted(p.name for p in (pool/'协同池对象').glob('*.o')),
        客户端来源=str(prior),说明='复用前端与未修改ZenFS对象，只重编空间交接两端并重新链接'))


def execute(args,password,output,binary,headers):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    run.state.update(边界='真实RocksDB定制db_bench的受控筛查，非正常并发性能',GC启用=True)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+
            [f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='恢复原软件及逐字复用已验收程序' if args.model_pair else
                   '恢复原软件，仅重编空间交接两端并链接原请求客户端')
        run.ssh(f'python3 {run.target}/restore_calibration_guest.py {run.target}',180)
        run.ssh(f'python3 {run.target}/build_pool_dbbench_guest.py {run.target}',900)
        for name in ('同步接线构建核验.json','真实DB构建核验.json'):
            run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/{name}',str(output/name)])
        require(json.loads((output/'同步接线构建核验.json').read_text())['真实线程协议自检通过'],
                '新对象空间交接自检未通过')
        fs=run.target+'/协同池副本/plugin/zenfs/fs/'
        remote=digest_manifest(run.ssh('sha256sum '+' '.join(shlex.quote(fs+n) for n in headers)))
        actual={Path(k).name:v for k,v in remote.items()}
        require(actual==headers,'设备与宿主评分核的实际依赖字节不同')
        stamp(output/'设备宿主核身份核验.json',dict(通过=True,实际头文件=actual,宿主重算SHA256=digest(binary)))
        run.record(阶段='真实RocksDB共同装载与SST标签布局核验')
        wait_stage(run,'source','run_pool_source_guest.sh',1800,mode='greedy')
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/真实SST布局核验.json',str(output/'真实SST布局核验.json')])
        start=None
        for tag in (('p1','p2','m1','m2') if args.model_pair else ('p1','p2')):
            greedy=tag.startswith('p');run.record(阶段=tag)
            wait_stage(run,tag,'run_pool_business_guest.sh',1800,mode='greedy' if greedy else 'blocks')
            path=collect(run,tag);report=arm(path,greedy,binary)
            report['空间交接']=space_handoff((path/'run.zenfs.log').read_text())
            stamp(output/(tag+'-核验.json'),report)
            if start is None:start=path
            else:
                proof=initial(start,path)
                require(all(proof[k]['相同'] for k in SEMANTIC),'真实数据库对照起点不同，停止')
                require(request_comparison(start,path)['逻辑提交相同'],'逻辑请求不同，停止')
            run.record(已完成=[*run.state.get('已完成',[]),tag])
            if tag in ('p2','m2'):
                first='p1' if tag=='p2' else 'm1'
                proof=repeat_guard(output/'逐臂采证'/first,path)
                stamp(output/(('Greedy' if greedy else '模型')+'重复核验.json'),proof)
                require(proof['通过'],'同策略重复未通过，停止收益比较并保留首差')
                run.record(本轮Greedy准入通过=True) if greedy else run.record(模型重复通过=True)
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
    if args.model_pair:proof=analyze(output,binary)
    else:
        proof=repeat_guard(output/'逐臂采证/p1',output/'逐臂采证/p2')
        require(proof['通过'],'Greedy重复未通过，不发布模型收益')
    run.record(阶段='真实db_bench筛查完成，已归档并关闭客机',完整核验通过=proof['通过'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='joint-controlled-pool-dbbench-v4-20261029')
    parser.add_argument('--model-pair',action='store_true',help='Greedy重复通过后继续模型；默认仅验收同步修复')
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法')
    admission(BASELINE)
    previous=IMAGE_DIR/'joint-controlled-cover-boundary-v1-20261029-results'
    require(json.loads((previous/'边界核验.json').read_text())['通过'],'前轮校准未验收')
    state=json.loads((previous/'状态.json').read_text());archive=json.loads((previous/'阶段归档.json').read_text())
    require(state['当前客机PID'] is None and archive['完整'] and archive['业务成功'],'前轮未归档退出')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'前轮归档哈希不符')
    # 本轮真实首差已证明，仅有微型实验的轮询批次门不足以做DB收益对照。
    verified=None
    if args.model_pair:verified=verified_database(VERIFIED_DB)
    else:
        deployed=MODEL_PREVIOUS/MODEL_PREVIOUS.name.removesuffix('-results')/'协同池副本/plugin/zenfs/fs'
        fixed=sync_patch((deployed/'fs_zenfs.cc').read_text(),(deployed/'zbd_zenfs.cc').read_text())
        require_ready(*fixed)  # 客机还须编译对象、自检并保存新构建身份。
    budget=preflight();check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'资源不足')
    budget.update(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30,
        说明='单台串行；独立完整ZenFS对象和归档；原200GiB项目上限及服务器保护线不变')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧实验')
    native=DONOR/DONOR.name.removesuffix('-results')
    wrappers={'run_pool_source_guest.sh':source_wrapper((native/'run_logical_source_guest.sh').read_text()),
              'run_pool_business_guest.sh':business_wrapper((native/'run_calibrated_guest.sh').read_text())}
    for text in wrappers.values():subprocess.run(['bash','-n'],input=text,text=True,check=True)
    if args.model_pair:
        for name,text in wrappers.items():
            require(text==(VERIFIED_DB/'源码'/name).read_text(),'负载或观测包装器与已验收轮不同')
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('真实数据库实验认证（仅内存）：');output.mkdir()
    freeze(output,restore_fix=True,reuse_build=PREVIOUS)
    reused=json.loads((BASELINE/'源码/复用诊断客户端.json').read_text())
    freeze_bundle(output,PREVIOUS,DONOR,reused,metadata_reference=False)
    for name in ('restore_calibration_guest.py','build_pool_dbbench_guest.py','build_all_blocks_guest.py',
        'build_all_blocks_dbbench_guest.py','flush_wait_source.py','calibration_source.py','dbbench_space_probe.cc',
        'pool_dbbench_guest.py','pool_dbbench_source.py','run_pool_dbbench_host.py',
        'build_pool_dbbench_sync_guest.py','pool_dbbench_sync_source.py','controlled_gc_source.py',
        'target_alloc_source.py','test_boundary_sync.cc',
        'logical_input_guest.py','calibration_guest.py','strict_stats_guest.py'):
        shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    for name in ('rrzc_tls_seed.h','rrzc_front_order.h','rrzc_io_trace.h','rrzc_boundary_sync.h',
        '校准-db_bench_tool.cc','诊断-flush_job.cc','诊断-sync_point.cc','诊断-sync_point_impl.cc'):
        shutil.copyfile(native/name,output/'源码'/name)
    for name in ('analyze_pool_dbbench.py','analyze_maintenance_cover.py','analyze_colocation_cases.py',
                 'audit_colocation_transactions.py','replay_maintenance_cover.py'):
        shutil.copyfile(ROOT/name,output/'源码'/name)
    labels=roles(table(native/'source/装载-0.sst.csv'))
    (output/'源码/受控标签.txt').write_text(''.join(name+' '+tag+'\n' for name,tag in sorted(labels.items())))
    stamp(output/'源码/受控标签来源.json',dict(来源=str(native/'source/装载-0.sst.csv'),标签=labels,
        参考SST=table(native/'source/装载-0.sst.csv'),说明='历史准备期实测SST键区间；不使用业务后的失效信息'))
    for name,text in wrappers.items():(output/'源码'/name).write_text(text)
    binary,headers=freeze_object(output,VERIFIED_DB if args.model_pair else MODEL_PREVIOUS)
    if args.model_pair:freeze_verified_programs(output,VERIFIED_DB,*verified)
    else:freeze_sync_components(output)
    stamp(output/'预注册计划.json',dict(目的='真实SST下检验布局、联合机会和模型实际改选，不预设正收益',平台=profile8(),
        运行顺序=['共同装载','p1-Greedy','p2-Greedy']+(['m1-模型','m2-模型'] if args.model_pair else []),
        受控空间请求确认=True,仅验收同步修复=not args.model_pair,
        固定模型=headers,固定K=1,原GC百分比=20,RR阈值=1024,候选资格='original',
        负载='既有定制db_bench轮转24批；917504键，4KiB值，786432读+589824写，固定全局顺序',
        起点='本轮K1实际装载后保存逻辑字节、Zone/WP及辅助目录；每臂Reset回放、实读准备RC并核验，不称NAND快照',
        标签='按历史准备期SST键范围冻结的H/W清单；装载后必须核验文件编号及键范围一致和混合布局；新输出无标签回退',
        缓存='Block/Row/Persistent缓存关闭，Direct I/O，WAL关闭；MemTable仍存在',
        调度='共享后台线程、写后排空、批次GC检查门；不代表正常并发性能',
        指标='逐请求逻辑输入、GC/RR、总复制、末端空间、平均API与P99/P99.9/P99.99、实际吞吐',
        准入='两次Greedy完整起点/输入/后台与维护/设备增量通过后才允许运行模型',
        未采集='冻结ZenFS对象未接逐请求设备摘要；不声称每个物理读命令一致',
        资源=dict(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30)))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,binary,headers)
    finally:password=None


if __name__=='__main__':main()
