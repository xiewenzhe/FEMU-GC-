"""串行运行普通/协同放置与Greedy/冻结模型四组合；默认关闭且只用临时模拟盘。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
from run_colocation_host import (admission,BASELINE,DONOR,BINARY,GUEST,CalibrationRun,
    PREVIOUS,PARENT,ROOT,IMAGE_DIR,check_baseline_budget,detach_supervisor,freeze,
    freeze_bundle,preflight,profile8,stamp,wait_stage,stop_business,preserve_unverified_image,digest,require)
from analyze_colocation_cases import compare_repeats,logical_requests
from analyze_colocation_choice import analyze,arm
from calibration_source import once


def wrapper():
    text=once(GUEST,'[[ $tag == reference || $tag == disabled || $tag == enabled ]]',
        '''[[ $tag =~ ^(smoke|choice)-k[01]-(greedy|model)-r[12]$ ]]
scenario=${tag%%-k*}
k=${tag#*-k}; k=${k%%-*}
greedy=1
if [[ $tag == *-model-* ]]; then greedy=0; fi''')
    text=once(text,' > "$result/mkfs.log"',' --enable_gc > "$result/mkfs.log"')
    # guarded影子入口与旧V1影子请求互斥；不能同时继承功能入口的旧开关。
    text=once(text,' ZENFS_RRZC_V1_SHADOW=1','')
    begin=text.index('program=$target/colocation_prototype\n');end=text.index('echo run > "$result/phase.txt"',begin)
    text=text[:begin]+text[end:]
    text=once(text,'  ZENFS_RRZC_POOL_K="$k"',
        '''  ZENFS_RRZC_CAL_GC_GATE="$result/受控GC" \\
  RRZC_CAL_COMMON_OBSERVATION=1 RRZC_CAL_GREEDY_EXECUTE="$greedy" \\
  ZENFS_RRZC_UNIT_COST=1 ZENFS_RRZC_GUARDED_MODE=shadow ZENFS_RRZC_COST_RANK=0 \\
  ZENFS_RRZC_RECENT_SAMPLER=1 ZENFS_RRZC_MIGRATION_WINDOW=1 ZENFS_RRZC_RETENTION_MODE=observe \\
  ZENFS_RRZC_DIRECT=1 ZENFS_RRZC_PAIR_GREEDY=0 ZENFS_RRZC_WINDOW_CREDIT=1 \\
  ZENFS_RRZC_PERSISTENT_CREDIT=1 ZENFS_RRZC_EXPECTED_RR=1 ZENFS_RRZC_CANDIDATE_POOL=original \\
  ZENFS_RRZC_ALL_BLOCKS=1 ZENFS_RRZC_SPACE_PLAN=1 ZENFS_RRZC_DEADLINE=1 \\
  ZENFS_RRZC_JOINT_TIME=1 ZENFS_RRZC_TERMINAL=0 \\
  ZENFS_RRZC_POOL_K="$k"''')
    return once(text,'  "$program" "$result" "$k" > "$result/run.log" 2>&1',
        '''  "$target/colocation_choice" "$scenario" "$result" "$k" > "$result/run.log" 2>&1 &
bench=$!
echo "$bench" > "$result/bench.pid"
wait "$bench"''')


def valid_tag(tag):
    require(re.fullmatch(r'(smoke|choice)-k[01]-(greedy|model)-r[12]',tag) is not None,'四组合标签非法')


def extract(archive,dest,tag):
    valid_tag(tag);require(not (dest/tag).exists(),'不覆盖逐臂证据')
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            p=Path(member.name)
            require(not p.is_absolute() and '..' not in p.parts and p.parts[0]==tag and
                len(p.parts)<=2 and (member.isfile() or (member.isdir() and p.parts==(tag,))),
                '逐臂证据成员越界或含链接/子目录')
        root=dest/tag;root.mkdir()
        for member in tar.getmembers():
            if member.isdir():continue
            with tar.extractfile(member) as source,(root/Path(member.name).name).open('xb') as out:
                shutil.copyfileobj(source,out)


def collect(run,tag):
    valid_tag(tag);remote=run.target+'/'+tag+'-evidence.tar.gz'
    run.ssh(f"test ! -e {remote} && tar --exclude='{tag}/aux' -czf {remote} -C {run.target} {tag}",120)
    expected=run.ssh('sha256sum '+remote).split()[0]
    dest=run.output/'逐臂采证';dest.mkdir(exist_ok=True);archive=dest/(tag+'.tar.gz')
    run.command(run.scp()+[f'femu@127.0.0.1:{remote}',str(archive)],180)
    require(digest(archive)==expected,'逐臂传输哈希不同');extract(archive,dest,tag);return dest/tag


def execute(args,password,output):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    run.state.update(边界='原候选、冻结模型、多候选四组合机制验证，非应用性能',GC启用=True)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+
            [f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='恢复并编译四组共同对象，原库不覆盖')
        run.ssh(f'python3 {run.target}/restore_calibration_guest.py {run.target}',180)
        run.ssh(f'python3 {run.target}/build_colocation_guest.py {run.target}',900)
        for k in (0,1):
            tag=f'smoke-k{k}-greedy-r1';run.record(阶段=tag)
            wait_stage(run,tag,'run_colocation_choice_guest.sh',180,mode='greedy')
            root=collect(run,tag);require('多候选布局小测通过' in (root/'run.log').read_text(),'布局小测失败')
        reference=None
        for k in (0,1):
            initial=None
            for mode in ('greedy','model'):
                roots=[]
                for r in (1,2):
                    tag=f'choice-k{k}-{mode}-r{r}';run.record(阶段=tag)
                    wait_stage(run,tag,'run_colocation_choice_guest.sh',600,mode='greedy')
                    root=collect(run,tag);stamp(output/(tag+'-核验.json'),arm(root,k,mode));roots.append(root)
                    requests=logical_requests(root)
                    if reference is None:reference=requests
                    require(reference==requests,'逻辑请求不同，停止')
                    run.record(已完成=[*run.state.get('已完成',[]),tag])
                repeat=compare_repeats(*roots);stamp(output/(f'k{k}-{mode}-重复.json'),repeat)
                require(repeat['通过'],'同配置重复出现首差，停止并归档')
                if initial is None:initial=roots[0]
                else:
                    from analyze_colocation_cases import normalize_blocks,table
                    require(normalize_blocks(initial,'prepared')==normalize_blocks(roots[0],'prepared') and
                        table(initial/'prepared.extents.csv')==table(roots[0]/'prepared.extents.csv'),
                        '同K不同选择器起点不同，停止')
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
    analyze(output);run.record(阶段='四组合重复及选区账目核验完成，已归档并关闭客机')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='joint-controlled-colocation-choice-v3-20261029')
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法')
    admission(BASELINE)
    previous=IMAGE_DIR/'joint-controlled-colocation-cases-v2-20261029-results'
    require(json.loads((previous/'单区Greedy完整事务补充核验.json').read_text())['通过'],'三情形完整事务未验收')
    budget=preflight();check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'资源不足')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧结果')
    subprocess.run(['bash','-n'],input=wrapper(),text=True,check=True)
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('四组合认证（仅内存）：');output.mkdir()
    freeze(output,restore_fix=True,reuse_build=PREVIOUS)
    reused=json.loads((BASELINE/'源码/复用诊断客户端.json').read_text())
    freeze_bundle(output,PREVIOUS,DONOR,reused,metadata_reference=False)
    files=('restore_calibration_guest.py','build_colocation_guest.py','colocation_source.py',
        'colocation_device.cc','colocation_cases_device.cc','colocation_choice_device.cc',
        'colocation_choice_source.py','unit_cost_source.py','matched_observation_source.py',
        'build_all_blocks_guest.py','calibration_source.py','controlled_gc_source.py',
        'run_colocation_choice_host.py','run_colocation_host.py','greedy_admission_archive.py')
    for name in files:shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    for name in ('rrzc_colocation_pool.h','rrzc_colocation_scope.h','rrzc_colocation_zenfs.inc','rrzc_unit_cost_selector.h'):
        shutil.copyfile(ROOT/'model'/name,output/'源码'/name)
    for name in ('analyze_colocation_choice.py','analyze_colocation_cases.py','audit_colocation_transactions.py'):
        shutil.copyfile(ROOT/name,output/'源码'/name)
    (output/'源码/启用协同池四组合').write_text('仅独立对象：original资格，冻结单位回收评分，共同观测\n')
    (output/'源码/受控标签.txt').write_text('/joint/H.sst H\n/joint/W.sst W\n')
    (output/'源码/run_colocation_choice_guest.sh').write_text(wrapper())
    stamp(output/'预注册计划.json',dict(目的='分离协同放置与冻结选择器贡献；不保证正收益',平台=profile8(),
        组合=['K0Greedy','K0模型','K1Greedy','K1模型'],重复=2,
        模型='原单位回收净复制评分与原全块/近期/留存估计，未新增权重',
        原候选资格=True,GC启动百分比=20,RR阈值=1024,
        输入='W96/H32/C96/D24/U104 MiB及52x128MiB；H读900后退役W/U，检查，再读312，再检查两次',
        读取节奏='9次100读，每批后显式等待1秒以取得原2~4秒采样；不是应用吞吐实验',
        核验='逐条请求、同K起点、同配置重复、实际候选评分、选择、提交、源Reset及总复制',
        空间='报告实际终态及Active；不预设收益，不以RR转ZC代替复制节省',
        资源=dict(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30,保护门槛不变=True)))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output)
    finally:password=None


if __name__=='__main__':main()
