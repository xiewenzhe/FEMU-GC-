"""普通/协同放置的单前台与后台重叠对照；每策略一臂，不重复Greedy。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import tarfile
from run_phase_time_host import (prepare as phase_prepare,execute,ROOT,IMAGE_DIR,BASELINE,
    PRIOR,GUEST_MODULES as PHASE_MODULES,admission,preflight,check_baseline_budget,
    check_observation,profile8,stamp,require,detach_supervisor,digest)
from overlap_source import wrapper

GUEST_MODULES=PHASE_MODULES+('overlap_source.py','build_overlap_guest.py','reuse_overlap_guest.py','restore_calibration_guest.py',
    'read_cost_source.py','build_read_cost_guest.py','end_to_end_source.py','build_end_to_end_guest.py')


def prepare(output,read_cost=False,end_to_end=False):
    scorer,target=phase_prepare(output);source=output/'源码'
    for n in GUEST_MODULES:shutil.copyfile(ROOT/'tests'/n,source/n)
    with tempfile.TemporaryDirectory(prefix='rrzc-overlap-import-') as tmp:
        for n in GUEST_MODULES:shutil.copyfile(source/n,Path(tmp)/n)
        subprocess.run(['python3','-I','-c',
            'import sys;sys.path.insert(0,sys.argv[1]);import build_overlap_guest,build_read_cost_guest,build_end_to_end_guest',tmp],check=True)
    p=source/'run_pool_business_guest.sh';p.write_text(wrapper(p.read_text()))
    if read_cost:
        from calibration_source import once
        p.write_text(once(p.read_text(),'RRZC_PHASE_TIME=',
            'RRZC_READ_COST="${request_trace:+$result/读取成本.bin}" RRZC_PHASE_TIME='))
        shutil.copyfile(ROOT/'model/rrzc_read_cost.h',source/'rrzc_read_cost.h')
        shutil.copyfile(ROOT/'analyze_read_cost.py',source/'analyze_read_cost.py')
    if end_to_end:
        from end_to_end_source import wrapper as end_wrapper
        p.write_text(end_wrapper(p.read_text()))
        shutil.copyfile(ROOT/'tests/rrzc_end_to_end.h',source/'rrzc_end_to_end.h')
        shutil.copyfile(ROOT/'analyze_end_to_end.py',source/'analyze_end_to_end.py')
    subprocess.run(['bash','-n',str(p)],check=True)
    for p in (Path(__file__),ROOT/'analyze_overlap.py'):
        shutil.copyfile(p,source/p.name)
    stamp(source/'本轮新增源码身份.json',{p.name:digest(p) for p in source.iterdir()
        if p.is_file() and p.name!='本轮新增源码身份.json'})
    return scorer,target


def attach_completed_greedy(output,previous,scorer):
    from result_storage import closed_archive
    from analyze_integrated_pool import arm
    from analyze_phase_time import analyze
    from analyze_overlap import validate
    previous=Path(previous).resolve();require(previous.parent==IMAGE_DIR.resolve(),'Greedy来源越界')
    state,archive=closed_archive(previous)
    require(state['镜像处理']['status']=='deleted' and not (previous/'逐臂采证/m1').exists(),
        '只接续已退出且仅完成Greedy的现场，不混入其他机制臂')
    path=previous/'逐臂采证/p1'
    report=arm(path,True,scorer,pool_k=0,concurrent=True)
    timing=analyze(path,concurrent=True);ledger=validate(path,report)
    old=previous/state['标签'];proof=json.loads((old/'前后台重叠构建.json').read_text())
    require(digest(old/'db_bench')==proof['程序SHA256'] and
        digest(old/'计时ZenFS.o')==proof['ZenFS对象SHA256'],'已完成Greedy的构建身份变化')
    stamp(output/'p1-核验.json',report);stamp(output/'p1-时间核验.json',timing)
    stamp(output/'p1-业务收尾核验.json',ledger)
    collected=output/'逐臂采证';collected.mkdir();(collected/'p1').symlink_to(path,target_is_directory=True)
    stamp(output/'已完成Greedy引用.json',dict(来源=str(previous),逐臂证据=str(path),
        来源归档=str(archive),来源归档SHA256=state['镜像处理']['archive_sha256'],
        原监督失败原因=state.get('原始错误'),重新核验通过=True,重新执行业务=False,
        说明='原业务和内容校验成功；旧离线审计缺少并发退役撤销分支；原归档及失败状态不改写'))
    source=output/'源码';payload=source/'重叠程序复用.tar.gz'
    files={n:old/n for n in ('db_bench','计时ZenFS.o','校准-db_bench_tool.cc','前后台重叠构建.json')}
    files.update({str(p.relative_to(old)):p for p in (old/'协同池副本').rglob('*') if p.is_file()})
    with tarfile.open(payload,'x:gz') as tar:
        for name,p in files.items():
            require(not p.is_symlink(),'复用软件含链接');tar.add(p,arcname=name,recursive=False)
    stamp(source/'重叠程序复用清单.json',dict(来源=str(previous),来源归档SHA256=state['镜像处理']['archive_sha256'],
        文件={n:digest(p) for n,p in files.items()},压缩包SHA256=digest(payload)))
    stamp(source/'本轮新增源码身份.json',{p.name:digest(p) for p in source.iterdir()
        if p.is_file() and p.name!='本轮新增源码身份.json'})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',default='joint-controlled-overlap-v1-20261105')
    p.add_argument('--detach',action='store_true');p.add_argument('--check',action='store_true')
    p.add_argument('--ftl-audit',action='store_true',help='沿用输入来源的共同诊断观测，不与旧轮混算性能')
    p.add_argument('--reuse-greedy',help='接续已完成但旧离线审计失败的Greedy；先重新核验，仅执行协同臂')
    p.add_argument('--read-cost',action='store_true',help='共同增加读锁/命令/前台记录成本观测，禁止混用旧计时')
    p.add_argument('--end-to-end',action='store_true',help='有界应用队列，写请求同步Flush后确认完成；两臂新语义')
    args=p.parse_args();require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法')
    admission(BASELINE);check_observation(PRIOR,args.ftl_audit)
    budget=preflight();used=check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'资源保护线不足')
    budget.update(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30,目录已占字节=used)
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖已有结果')
    if args.check:print(json.dumps(budget,ensure_ascii=False));return
    password=getpass.getpass('前后台重叠实验认证（仅内存）：');output.mkdir()
    require(not(args.read_cost and args.reuse_greedy),'新观测必须两臂统一，不引用旧Greedy计时')
    require(not(args.end_to_end and (args.read_cost or args.reuse_greedy)),
        '新完成语义不混入旧臂或逐读诊断')
    scorer,target=prepare(output,read_cost=args.read_cost,end_to_end=args.end_to_end)
    if args.reuse_greedy:attach_completed_greedy(output,args.reuse_greedy,scorer)
    plan=dict(平台=profile8(),每臂请求=2260992,读=786432,写=1474560,
        窗口=12,顺序=['p1普通K0＋Greedy','m1协同K1＋Greedy'],额外Greedy重复组=False,
        前台='单线程固定全局逻辑请求顺序，closed-loop，无QPS限速',
        后台='独立HIGH/LOW线程；每2048写异步Flush；业务不WaitForCompact；原10秒GC，无批次门控',
        收尾='请求结束后同步Flush与WaitForCompact，维护日志边界分账，计入关闭前完整迁移',
        RR=1024,GC启动百分比=20,原候选资格=True,选区='两臂Greedy',
        缓存=False,WAL=False,MemTable=True,资源=budget,完整设备追踪=args.ftl_audit,
        输入来源=str(PRIOR),读取成本观测=args.read_cost,应用队列端到端=args.end_to_end,
        边界='前后台实际交错允许不同；非多客户端并发，不作统计显著性结论')
    if args.end_to_end:
        plan.update(应用队列容量=4096,产生时点='准备入队前，包含队列满时的阻塞；无QPS限速',
            写完成='每2048写同步Flush成功后同批逐请求确认；不是Put返回，不均摊耗时',
            读完成='FIFO取出执行Get、数据校验成功返回',
            后台='HIGH/LOW各1，原10秒GC；逐批同步Flush，只在末尾排空Compaction',
            可靠性边界='RocksDB/ZenFS及模拟设备确认完成口径，不证明宿主或模拟NAND断电恢复')
    stamp(output/'预注册计划.json',plan)
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,scorer,target,overlap=True,completed_greedy=args.reuse_greedy,
        read_cost=args.read_cost,end_to_end=args.end_to_end)
    finally:password=None


if __name__=='__main__':main()
