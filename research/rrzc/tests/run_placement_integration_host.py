"""第三阶段完整构建及小测；独立客机、原库不改，采证后正常关机回收临时盘。"""
import argparse
import getpass
import json
from pathlib import Path
import shutil
import signal
import shlex
import subprocess
import tarfile
from types import SimpleNamespace
from run_calibration_host import CalibrationRun,detach_supervisor,check_baseline_budget
from run_all_blocks_followup_host import PARENT
from run_pool_dbbench_host import BINARY
from run_rr_path_host import preflight,ROOT,IMAGE_DIR
from run_ycsb_quick_host import profile8
from ycsb_af import digest,require
from result_storage import configure,compact
from result_storage import closed_archive
from placement_device_source import extend
from analyze_placement_integration import analyze as composition_audit
from run_dbbench_parallel_host import stamp

SOURCE=IMAGE_DIR/'joint-controlled-end-to-end-v1-20261105-results'
SECOND=ROOT/'results/placement-ablation-20261105/阶段二'


def prepare(output,exit_fix=False):
    old=SOURCE/'joint-controlled-integrated-pool-v2-20261104'
    identity=json.loads((SECOND/'副本身份.json').read_text())
    require(digest(Path(identity['原程序']))==identity['原程序SHA256'],'旧程序身份变化')
    for name,h in identity['新源码'].items():require(digest(SECOND/'新版源码'/name)==h,'冻结二阶源码变化')
    for rel,h in json.loads((SECOND/'交付文件SHA256.json').read_text()).items():
        require(digest(SECOND/rel)==h,'阶段二归档变化')
    source=output/'源码';source.mkdir()
    fs=source/'协同池副本/plugin/zenfs/fs';fs.mkdir(parents=True)
    for p in (SECOND/'新版源码').iterdir():shutil.copyfile(p,fs/p.name)
    shutil.copyfile(SECOND/'副本身份.json',source/'二阶副本身份.json')
    if exit_fix:
        header=ROOT/'model/rrzc_placement_trace_exit.h'
        shutil.copyfile(header,fs/'rrzc_placement_trace.h')
        zbd=fs/'zbd_zenfs.h';text=zbd.read_text()
        anchor='  uint64_t composition_device_=rrzc_placement_trace::NewId();'
        require(text.count(anchor)==1,'设备生命周期观察锚点变化')
        zbd.write_text(text.replace(anchor,'  rrzc_placement_trace::DeviceLease composition_lease_;\n'+anchor,1))
        identity['新源码']['rrzc_placement_trace.h']=digest(header)
        identity['新源码']['zbd_zenfs.h']=digest(zbd)
        identity['退出封存修复']=dict(原版身份=digest(SECOND/'副本身份.json'),
            修复头SHA256=digest(header),仅观测器生命周期=True,
            观测终点='atexit请求封存；全部设备对象释放后最终封存，包含全局Env文件清理；不改变实际文件释放')
        stamp(source/'二阶副本身份.json',identity)
    headers=json.loads((old/'原客户端头文件身份.json').read_text())
    for name,h in headers.items():
        require(digest(old/name)==h,'原客户端头身份变化')
        # 文件内嵌头优先当前目录；重名头必须统一，否则可能违反ODR并漏识别W。
        shutil.copyfile(fs/name if (fs/name).exists() else old/name,source/name)
    for name in ('rrzc_end_to_end.h','rrzc_phase_time.h'):
        shutil.copyfile(fs/name if (fs/name).exists() else old/name,source/name)
    for name in ('校准-db_bench_tool.cc','诊断-flush_job.cc','诊断-sync_point.cc.o','诊断-sync_point_impl.cc.o'):
        shutil.copyfile(old/name,source/name)
    commands=['计时编译-'+n for n in ('fs_zenfs','io_zenfs','zbd_zenfs','zbdlib_zenfs','zonefs_zenfs')]
    commands+=['计时客户端编译','计时客户端链接']
    for name in commands:shutil.copyfile(old/(name+'.命令.json'),source/(name+'.原命令.json'))
    for name in ('build_placement_integrated_guest.py','run_placement_device_guest.py'):
        shutil.copyfile(ROOT/'tests'/name,source/name)
    (source/'放置设备小测.cc').write_text(extend((ROOT/'tests/rr_path_device.cc').read_text()))
    manifest={str(p.relative_to(source)):digest(p) for p in source.rglob('*') if p.is_file()}
    stamp(source/'传输身份.json',manifest)
    payload=output/'传输.tar.gz'
    with tarfile.open(payload,'x:gz') as archive:
        for p in source.rglob('*'):
            if p.is_file():archive.add(p,arcname=str(p.relative_to(source)),recursive=False)
    stamp(output/'构建来源.json',dict(二阶身份=digest(SECOND/'副本身份.json'),旧程序SHA256=identity['原程序SHA256'],
        原库SHA256=identity['原库SHA256'],请求源码SHA256=digest(old/'校准-db_bench_tool.cc'),
        传输SHA256=digest(payload),配置='8GiB、128MiB、Open8/Active12、RR1024，Greedy'))
    return payload


def prepare_reuse(output,previous):
    previous=Path(previous).resolve();require(previous.parent==IMAGE_DIR,'复用来源越界')
    state,archive=closed_archive(previous)
    old=previous/state['标签'];proof=json.loads((old/'集成构建核验.json').read_text())
    require(proof['通过'] and proof['完整五单元编译'],'来源没有完成独立集成构建')
    source=output/'源码';source.mkdir()
    for name,key in [('db_bench','程序SHA256'),('placement_probe','设备小测SHA256')]:
        require(digest(old/name)==proof[key],'已完成构建程序身份不符');shutil.copy2(old/name,source/name)
        require((source/name).stat().st_mode&0o111,'复用程序缺少执行权限')
    shutil.copyfile(old/'集成构建核验.json',source/'集成构建核验.json')
    shutil.copyfile(ROOT/'tests/run_placement_device_guest.py',source/'run_placement_device_guest.py')
    stamp(source/'传输身份.json',{p.name:digest(p) for p in source.iterdir() if p.is_file()})
    payload=output/'传输.tar.gz'
    with tarfile.open(payload,'x:gz') as tar:
        for p in source.iterdir():tar.add(p,arcname=p.name,recursive=False)
    stamp(output/'构建来源.json',dict(复用来源=str(previous),归档SHA256=digest(archive),
        程序SHA256=proof['程序SHA256'],设备小测SHA256=proof['设备小测SHA256'],重新编译=False))
    return payload


def execute(args,password,output,payload):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=True;run.profile=profile8();run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.state.update(边界='第三阶段独立完整构建及功能小测，不是消融收益实验',GC启用=False)
    configure(run,ftl_audit=False)
    policy=run.state['结果保存策略'];policy.update(本轮归档含输入字节=False,
        输入说明='功能载荷由归档程序与固定CLI生成；未保存完整NAND或逐请求业务输入检查点')
    stamp(output/'结果保存策略.json',policy);run.record(结果保存策略=policy)
    # 不归档中间对象和传输包；保留程序、命令、源码身份及测试证据。
    run.archive_exclusions+=('传输.tar.gz','计时对象/*.o','计时客户端.o','计时ZenFS.o',
        '诊断-flush_job.cc.o','诊断-sync_point.cc.o','诊断-sync_point_impl.cc.o','计时链接映射.txt')
    error=None
    try:
        run.start();run.ssh(f'mkdir {run.target}');run.deployed=True
        run.command(run.scp()+[str(payload),f'femu@127.0.0.1:{run.target}/传输.tar.gz'])
        require(run.ssh(f'sha256sum {run.target}/传输.tar.gz').split()[0]==digest(payload),'传输内容变化')
        run.ssh(f'tar -xzf {run.target}/传输.tar.gz -C {run.target}')
        if not args.reuse_program:
            run.record(阶段='完整重编五单元、Flush支持及客户端')
            run.ssh(f'python3 {run.target}/build_placement_integrated_guest.py {run.target}',900)
        else:
            run.record(阶段='核验并复用同一新程序，客机及内存NAND独立新建')
            remote=json.loads(run.ssh(f'cat {run.target}/传输身份.json'))
            for name,h in remote.items():require(run.ssh(f'sha256sum {run.target}/{name}').split()[0]==h,'客机部署身份不符')
            run.ssh(f'test -x {run.target}/db_bench && test -x {run.target}/placement_probe')
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/集成构建核验.json',str(output/'集成构建核验.json')])
        run.record(阶段='G2普通文件系统小测，观测开启及内容校验')
        run.ssh(f'env -i PATH=/usr/bin:/bin LANG=C.UTF-8 ZENFS_RRZC_PLACEMENT_MODE=G2 '
            f'ZENFS_RRZC_PLACEMENT_TRACE={run.target}/普通目录组成.bin '
            f'{run.target}/db_bench --db={run.target}/posix --benchmarks=fillseq,readseq,verifychecksum '
            '--num=1024 --reads=1024 --value_size=4096 --compression_type=none --disable_wal=1 '
            f'--cache_size=0 --row_cache_size=0 --threads=1 --seed=20261023 > {run.target}/普通目录小测.log 2>&1',120)
        if args.mode:
            run.record(阶段=args.mode+'独立空盘RocksDB、纯RR及重挂载功能测试',当前策略=args.mode)
            run.root(f'python3 {run.target}/run_placement_device_guest.py {run.target} {args.mode}',180)
        run.record(阶段='完整集成构建及普通目录小测通过，准备采证关机',已完成=['构建','普通目录小测'])
    except BaseException as e:
        error=e;run.record(原始错误=repr(e));raise
    finally:
        if run.deployed:
            # root设备测试产生0600事件文件；只修复本轮bin证据所有者，保持内容哈希。
            fix="from pathlib import Path;import os,json,hashlib;r=Path("+repr(run.target)+");o=r.stat();rows=[]\n"
            fix+="for p in r.rglob('*.bin'):\n if p.is_file() and not p.is_symlink():\n  h=hashlib.sha256(p.read_bytes()).hexdigest();os.chown(p,o.st_uid,o.st_gid);assert hashlib.sha256(p.read_bytes()).hexdigest()==h;rows.append([str(p.relative_to(r)),h])\n"
            fix+="(r/'采证权限核验.json').write_text(json.dumps(rows,ensure_ascii=False))"
            try:run.root('python3 -c '+shlex.quote(fix),60)
            except Exception as e:run.record(采证权限反馈=repr(e))
        run.finish(error)
        # 成功或失败都在归档退出核验后释放仅用于传输的本轮包。
        saved=run.state.get('镜像处理',{})
        if payload.exists() and saved.get('archive') and digest(Path(saved['archive']))==saved.get('archive_sha256'):
            payload.unlink()
    run.record(结果收尾=compact(output),阶段='集成构建归档并正常退出')
    if payload.exists():payload.unlink()
    if args.mode:
        local=output/run.state['标签']
        require(json.loads((local/'设备功能完成.json').read_text())['通过'],'设备功能未完成')
        reports={name:composition_audit(local/(name+'组成.bin')) for name in ('数据库','RR')}
        for name,report in reports.items():stamp(output/(name+'组成核验.json'),report)
        log=(local/'RR与重挂载小测.log').read_text()
        require('重挂载、完整内容和删除映射验证通过' in log,'重挂载内容未通过')
        rrlog=(local/'RR功能/rr.zenfs.log').read_text()
        require(rrlog.count('RR 完成：')==2 and reports['RR']['有效复制字节']==6<<20,'两次RR复制账目不符')
        require(reports['RR']['原生映射核验次数']>0,'没有原生映射独立核验')
        pool=[t for t in reports['数据库']['目标'] if t['协同目标'] and t['标签']&2]
        if args.mode=='G0':require(not any(t['协同目标'] for r in reports.values() for t in r['目标']),'G0进入协同池')
        else:require(bool(pool),'真实Flush来源没有进入协同池')
        if args.mode=='G1':require(len(reports['RR']['G1拒绝'])>=2,'G1没有拒绝必要迁移H')
        if args.mode=='G2':require(any(t['协同目标'] and t['标签']&1 for t in reports['RR']['目标']),'G2没有接受必要迁移H')
        stamp(output/'设备集成核验.json',dict(通过=True,模式=args.mode,RR完成=2,RR有效复制字节=6<<20,
            Flush入池=len(pool),H拒绝=len(reports['RR']['G1拒绝']),
            原生映射核验次数=reports['RR']['原生映射核验次数'],真实重挂载内容=True,完整业务实验=False))
        run.record(阶段=args.mode+'设备功能及离线组成核验通过，已归档退出')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',default='joint-controlled-placement-build-v1-20261106')
    p.add_argument('--detach',action='store_true');p.add_argument('--check',action='store_true')
    p.add_argument('--mode',choices=('G0','G1','G2'))
    p.add_argument('--reuse-program',type=Path)
    p.add_argument('--exit-fix',action='store_true',help='仅独立副本应用已通过ASan回归的观测器退出封存修复')
    args=p.parse_args();require(args.label.startswith('joint-controlled-placement-') and
        all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.label),'标签非法')
    budget=preflight();require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'完整构建资源保护线不足')
    budget.update(新增磁盘预算字节=8<<30,目录已占字节=check_baseline_budget(8<<30),
        说明='仅五单元/Flush/客户端独立重编与小测，不重建静态库和完整装载；8GiB预算，200GiB总上限和原保护线不变')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖已有结果')
    if args.check:print(json.dumps(budget,ensure_ascii=False));return
    require(not args.reuse_program or args.mode,'复用程序只用于明确模式的独立设备验证')
    require(not(args.reuse_program and args.exit_fix),'修复版必须统一重编，不能混用旧对象')
    password=getpass.getpass('第三阶段独立构建认证（仅内存）：');output.mkdir()
    payload=prepare_reuse(output,args.reuse_program) if args.reuse_program else prepare(output,exit_fix=args.exit_fix)
    stamp(output/'预注册计划.json',dict(阶段='完整集成构建和功能小测',资源=budget,
        独立临时镜像=True,原库不修改=True,无完整业务实验=True,模式=args.mode or 'G2普通目录',
        设备验证=bool(args.mode),缓存=False,WAL=False,固定种子=20261023,
        设备初始状态='每组从相同父镜像新启动，全空8GiB FEMU内存盘；qcow2非NAND快照',
        纯RR='功能验证阶段不启用GC，RR1024和安全规则不变；不是业务消融配置'))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,payload)
    finally:password=None


if __name__=='__main__':main()
