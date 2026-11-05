"""先验证布局，再串行两次重复三种情形；仅可销毁模拟盘，不修改选择器。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
from run_colocation_host import (admission,BASELINE,DONOR,BINARY,GUEST as ORIGINAL,
    CalibrationRun,PREVIOUS,PARENT,ROOT,IMAGE_DIR,check_baseline_budget,
    detach_supervisor,freeze,freeze_bundle,preflight,profile8,stamp,wait_stage,
    stop_business,preserve_unverified_image,digest,require)
from analyze_colocation_cases import analyze,arm,compare_repeats,logical_requests,CASES


def wrapper():
    text=ORIGINAL.replace('[[ $tag == reference || $tag == disabled || $tag == enabled ]]',
        '''[[ $tag =~ ^(smoke|w-first|rr-first|cooled)-k[01]-r[12]$ ]]
scenario=${tag%-k*}
k=${tag#*-k}; k=${k%%-*}''',1)
    text=text.replace(' > "$result/mkfs.log"',' --enable_gc > "$result/mkfs.log"',1)
    begin=text.index('program=$target/colocation_prototype\n')
    end=text.index('echo run > "$result/phase.txt"',begin)
    text=text[:begin]+text[end:]
    text=text.replace('  ZENFS_RRZC_POOL_K="$k"',
        '  ZENFS_RRZC_CAL_GC_GATE="$result/受控GC" ZENFS_RRZC_POOL_K="$k"',1)
    text=text.replace('  "$program" "$result" "$k" > "$result/run.log" 2>&1',
        '''  "$target/colocation_cases" "$scenario" "$result" "$k" > "$result/run.log" 2>&1 &
bench=$!
echo "$bench" > "$result/bench.pid"
wait "$bench"''',1)
    return text


def collect(run,tag):
    # 单独限定三情形命名，不扩大旧模型准入工具的组名白名单。
    require(re.fullmatch(r'(smoke|w-first|rr-first|cooled)-k[01]-r[12]',tag) is not None,'采证标签非法')
    remote=run.target+'/'+tag+'-evidence.tar.gz'
    run.ssh(f"test ! -e {remote} && tar --exclude='{tag}/aux' -czf {remote} -C {run.target} {tag}",120)
    expected=run.ssh('sha256sum '+remote).split()[0]
    dest=run.output/'逐臂采证';dest.mkdir(exist_ok=True)
    archive=dest/(tag+'.tar.gz')
    run.command(run.scp()+[f'femu@127.0.0.1:{remote}',str(archive)],180)
    require(digest(archive)==expected,'逐臂证据传输哈希不同')
    extract(archive,dest,tag)
    return dest/tag


def extract(archive,dest,tag):
    require(re.fullmatch(r'(smoke|w-first|rr-first|cooled)-k[01]-r[12]',tag) is not None,'采证标签非法')
    require(not (dest/tag).exists(),'不覆盖已有逐臂证据')
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            path=Path(member.name)
            require(not path.is_absolute() and '..' not in path.parts and path.parts[0]==tag and
                len(path.parts)<=2 and (member.isfile() or (member.isdir() and path.parts==(tag,))),
                '逐臂证据含目录、链接或越界成员')
        root=dest/tag;root.mkdir()
        for member in tar.getmembers():
            if member.isdir():continue
            with tar.extractfile(member) as source,(root/Path(member.name).name).open('xb') as output:
                shutil.copyfileobj(source,output)


def check_smoke(root):
    require((root/'wrapper.exit').read_text().strip()=='0' and
        '布局最小测试通过' in (root/'run.log').read_text(),'真实布局最小测试未通过')


def execute(args,password,output):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    run.state.update(边界='固定K三情形文件级因果验证，原Greedy及原20%资格，共同受控检查，非性能收益',GC启用=True)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+
                    [f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='恢复软件并独立编译三情形载荷及共同受控ZenFS对象')
        run.ssh(f'python3 {run.target}/restore_calibration_guest.py {run.target}',180)
        run.ssh(f'python3 {run.target}/build_colocation_guest.py {run.target}',900)
        for k in (0,1):
            tag=f'smoke-k{k}-r1';run.record(阶段=tag+'真实小测')
            wait_stage(run,tag,'run_colocation_cases_guest.sh',180,mode='greedy')
            check_smoke(collect(run,tag))
        for scenario in CASES:
            done={}
            for k in (0,1):
                for r in (1,2):
                    tag=f'{scenario}-k{k}-r{r}'
                    run.record(阶段=tag+'受控事件及空间账目验证')
                    wait_stage(run,tag,'run_colocation_cases_guest.sh',600,mode='greedy')
                    root=collect(run,tag);proof=arm(root,scenario,k)
                    stamp(output/(tag+'-核验.json'),proof);done[k,r]=root
                    run.record(已完成=[*run.state.get('已完成',[]),tag])
                repeat=compare_repeats(done[k,1],done[k,2])
                stamp(output/(scenario+f'-k{k}-重复.json'),repeat)
                require(repeat['通过'],'同情形独立重复出现分歧，停止后续实验，保留首差')
            require(logical_requests(done[0,1])==logical_requests(done[1,1]),'放置两组逻辑工作量不同')
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
    proof=analyze(output)
    run.record(阶段='三情形重复及净空间账目核验结束，已归档并正常关机',
        三情形对照=proof['对照'],边界=proof['边界'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='joint-controlled-colocation-cases-v1-20261029')
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法')
    admission(BASELINE)
    previous=IMAGE_DIR/'joint-controlled-colocation-k1-v1-20261029-results'
    require(json.loads((previous/'固定K1功能核验.json').read_text())['通过'],'固定K功能尚未验收')
    budget=preflight();check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'资源预算不足')
    budget.update(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30,
        说明='独立完整ZenFS对象和受控载荷编译；复用原静态库，单台客机串行')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧结果')
    subprocess.run(['bash','-n'],input=wrapper(),text=True,check=True)
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('三情形实验认证（仅内存）：')
    output.mkdir();freeze(output,restore_fix=True,reuse_build=PREVIOUS)
    reused=json.loads((BASELINE/'源码/复用诊断客户端.json').read_text())
    freeze_bundle(output,PREVIOUS,DONOR,reused,metadata_reference=False)
    for name in ('restore_calibration_guest.py','build_colocation_guest.py','colocation_source.py',
                 'colocation_device.cc','colocation_cases_device.cc','build_all_blocks_guest.py',
                 'calibration_source.py','controlled_gc_source.py',
                 'run_colocation_cases_host.py','run_colocation_host.py','greedy_admission_archive.py'):
        shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    for name in ('rrzc_colocation_pool.h','rrzc_colocation_scope.h','rrzc_colocation_zenfs.inc'):
        shutil.copyfile(ROOT/'model'/name,output/'源码'/name)
    shutil.copyfile(ROOT/'analyze_colocation_cases.py',output/'源码/analyze_colocation_cases.py')
    shutil.copyfile(ROOT/'audit_colocation_transactions.py',output/'源码/audit_colocation_transactions.py')
    (output/'源码/启用协同池三情形').write_text('共同受控检查门，原资格/选择/阈值不变；同程序K0/K1\n')
    (output/'源码/受控标签.txt').write_text('/joint/H.sst H\n/joint/W.sst W\n')
    (output/'源码/run_colocation_cases_guest.sh').write_text(wrapper())
    stamp(output/'预注册计划.json',dict(目的='验证固定协同布局的净维护收益和退化条件',平台=profile8(),
        选择器='两组均既有单区有效量Greedy，模型不启用',
        顺序=['两个布局最小测试',*[f'{s}-k{k}-r{r}' for s in CASES for k in (0,1) for r in (1,2)]],
        业务='相同W96MiB、H32MiB、C96MiB和52个128MiB填充文件；相同标签以外逻辑请求',
        先后关系=dict(w_first='700次读后退役W、检查，再512次读',
                      rr_first='1212次读后退役W、检查',cooled='700次读后退役W、检查，不再热读'),
        条件='共同20%触发及原候选资格；检查时点显式受控，不代表10秒周期并发性能',
        净空间='每臂最后有效6784MiB、空闲1024MiB、满区可回收0；不满足停止，不强制指定victim',
        成功判据='真实布局、请求、数据区起点、事务账目、终态空间与同配置重复通过；不要求正收益',
        预测边界='先后关系由受控输入安排；标签不是在线预测；只统计完整业务窗，校验另列',资源=budget))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output)
    finally:password=None


if __name__=='__main__':main()
