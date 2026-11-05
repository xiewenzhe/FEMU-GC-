"""真实SST的W覆盖/H保留机会筛查；只运行两个Greedy，不发布模型收益。"""
import argparse
import getpass
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
from run_pool_dbbench_host import (BASELINE,DONOR,BINARY,CalibrationRun,PREVIOUS,PARENT,ROOT,IMAGE_DIR,
    admission,check_baseline_budget,detach_supervisor,freeze,freeze_bundle,preflight,profile8,stamp,
    wait_stage,stop_business,preserve_unverified_image,digest,require,VERIFIED_DB,verified_database,
    freeze_object,collect,SEMANTIC,initial)
from sst_lifetime_source import wrapper,patched
from analyze_pool_dbbench import arm,repeat_guard,space_handoff
from analyze_sst_lifetime import timing,analyze as analyze_opportunities
from pool_dbbench_guest import roles
from calibration_source import once


def execute(args,password,output,binary):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=True;run.ftl_audit_binary=str(BINARY);run.ftl_audit_sha256=digest(BINARY)
    run.profile=profile8();run.args_reuse_image=IMAGE_DIR/(args.label+'-calibration.qcow2')
    run.state.update(边界='仅Greedy真实SST生命周期筛查；实验条件变化须重新核验；非性能收益实验',GC启用=True,
        固定H=args.persistent_h,标签范围='窗口0' if args.cohort_zero else '六窗',采样范围修正=args.sampling_scope)
    error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        run.command(run.scp()+[str(p) for p in (output/'源码').iterdir() if p.is_file()]+[f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='独立重编采样翻译单元并复用请求对象，最小验证' if args.sampling_scope else
            '恢复冻结ZenFS，逐字复用客户端并做普通目录小测' if args.reuse_client else
            '恢复冻结ZenFS，仅编译新生命周期客户端并做普通目录小测')
        run.ssh(f'python3 {run.target}/restore_calibration_guest.py {run.target}',180)
        builder='build_sampling_scope_guest.py' if args.sampling_scope else 'build_sst_lifetime_guest.py'
        run.ssh(f'python3 {run.target}/{builder} {run.target}',900)
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/生命周期构建核验.json',str(output/'生命周期构建核验.json')])
        require(json.loads((output/'生命周期构建核验.json').read_text())['普通目录小测通过'],'客户端最小测试失败')
        run.record(阶段='相同装载请求，验证真实SST混合布局')
        wait_stage(run,'source','run_pool_source_guest.sh',1800,mode='greedy')
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/真实SST布局核验.json',str(output/'真实SST布局核验.json')])
        for tag in ('p1','p2'):
            run.record(阶段=tag+'生命周期筛查')
            wait_stage(run,tag,'run_pool_business_guest.sh',1800,mode='greedy')
            path=collect(run,tag);report=arm(path,True,binary,reads=393216,writes=737280,timing_reader=timing)
            report['空间交接']=space_handoff((path/'run.zenfs.log').read_text())
            require('SST生命周期完成：读=393216，写=737280，检查点=60' in (path/'run.log').read_text(),
                    '生命周期负载没有完成固定请求数')
            if args.sampling_scope:
                log=(path/'run.zenfs.log').read_text()
                require('全块采样范围：未满区=1，代表诊断=1' in log and '全块代表门控：' in log,
                    '采样开关或实际代表块诊断未生效')
            stamp(output/(tag+'-核验.json'),report)
            run.record(已完成=[*run.state.get('已完成',[]),tag])
        proof=repeat_guard(output/'逐臂采证/p1',output/'逐臂采证/p2',request_count=1130496)
        stamp(output/'Greedy重复核验.json',proof)
        require(proof['通过'],'新负载Greedy重复未通过；保留首差，不作机会收益推断')
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
    analyze_opportunities(output)
    run.record(阶段='生命周期Greedy两次及机会分析完成，已归档并关闭客机',重复通过=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='joint-controlled-sst-lifetime-v4-20261029')
    parser.add_argument('--persistent-h',action='store_true',help='所有读批固定读取窗口0的4096个H键，W与请求数不变')
    parser.add_argument('--cohort-zero',action='store_true',help='仅窗口0全部H/W进入协同池；改变放置，重新核验两次Greedy')
    parser.add_argument('--sampling-scope',action='store_true',help='独立重编采样单元，两臂统一启用未满区C4历史与代表诊断')
    parser.add_argument('--reuse-client',type=Path,help='仅复用完整归档退出的相同生命周期源码与冻结ZenFS客户端')
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    args=parser.parse_args();require(re.fullmatch('joint-controlled-[a-z0-9-]+',args.label),'标签非法')
    require(not args.cohort_zero or (args.persistent_h and args.reuse_client),
        '窗口0配对实验要求固定H并逐字复用已验收客户端，不混入请求或构建变化')
    require(not args.sampling_scope or args.cohort_zero,'采样复测固定沿用窗口0配对，不同时改变其他负载条件')
    admission(BASELINE);guest,build=verified_database(VERIFIED_DB)
    previous=IMAGE_DIR/'joint-controlled-pool-dbbench-v5-20261029-results'
    state=json.loads((previous/'状态.json').read_text());archive=json.loads((previous/'阶段归档.json').read_text())
    require(json.loads((previous/'真实DB核验.json').read_text())['通过'] and archive['完整'] and
        archive['业务成功'] and not archive['客机仍存活'] and state['当前客机PID'] is None,'前轮对照未完整验收')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'前轮归档哈希不同')
    budget=preflight();check_baseline_budget(16<<30)
    require(budget['data可用字节']>=42<<30 and budget['可用内存字节']>=30<<30,'保护余量不足')
    budget.update(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30,
        说明='单台串行、独立客户端编译及完整采证；200GiB目录上限和服务器保护线不变')
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖结果')
    native=DONOR/DONOR.name.removesuffix('-results')
    wrappers={name:(VERIFIED_DB/'源码'/name).read_text() for name in ('run_pool_source_guest.sh','run_pool_business_guest.sh')}
    wrappers['run_pool_business_guest.sh']=wrapper(wrappers['run_pool_business_guest.sh'],args.persistent_h)
    if args.sampling_scope:
        wrappers={name:once(text,'ZENFS_MAINT_DIAG=1',
            'ZENFS_RRZC_SAMPLE_PARTIAL=1 ZENFS_RRZC_BLOCK_DIAG=1 ZENFS_MAINT_DIAG=1') for name,text in wrappers.items()}
    for text in wrappers.values():subprocess.run(['bash','-n'],input=text,text=True,check=True)
    patched((guest/'校准-db_bench_tool.cc').read_text())
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('生命周期筛查认证（仅内存）：');output.mkdir()
    freeze(output,restore_fix=True,reuse_build=PREVIOUS)
    reused=json.loads((BASELINE/'源码/复用诊断客户端.json').read_text())
    freeze_bundle(output,PREVIOUS,DONOR,reused,metadata_reference=False)
    for name in ('restore_calibration_guest.py','build_pool_dbbench_guest.py','build_all_blocks_guest.py',
        'build_all_blocks_dbbench_guest.py','flush_wait_source.py','calibration_source.py','dbbench_space_probe.cc',
        'pool_dbbench_guest.py','logical_input_guest.py','calibration_guest.py','strict_stats_guest.py','metadata_start_guest.py',
        'build_sst_lifetime_guest.py','sst_lifetime_source.py','rrzc_sst_lifetime.h','run_sst_lifetime_host.py'):
        shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    if args.sampling_scope:
        for name in ('build_sampling_scope_guest.py','all_blocks_scope_source.py','test_all_blocks_late_full.cc'):
            shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    for name in ('rrzc_tls_seed.h','rrzc_front_order.h','rrzc_io_trace.h','rrzc_boundary_sync.h',
        '校准-db_bench_tool.cc','诊断-flush_job.cc','诊断-sync_point.cc','诊断-sync_point_impl.cc'):
        shutil.copyfile(native/name,output/'源码'/name)
    for name in ('受控标签.txt','受控标签来源.json'):
        shutil.copyfile(VERIFIED_DB/'源码'/name,output/'源码'/name)
    if args.cohort_zero:
        source=output/'源码/受控标签来源.json';planned=json.loads(source.read_text());original_sha=digest(source)
        selected=roles(planned['参考SST'],True)
        require(len(selected)==32 and list(selected.values()).count('H')==2 and
            list(selected.values()).count('W')==30 and all(planned['标签'][n]==v for n,v in selected.items()),
            '窗口0标签与预注册的完整键范围不符，禁止按结果挑选SST')
        planned.update(标签=selected,标签范围='窗口0',原参考清单SHA256=original_sha,
            规则='只按预先声明的窗口0范围，包含全部30个W0；其他文件普通分配，不按已退役文件筛选')
        stamp(source,planned)
        (output/'源码/受控标签.txt').write_text(''.join(name+' '+tag+'\n' for name,tag in sorted(selected.items())))
    for name,text in wrappers.items():(output/'源码'/name).write_text(text)
    for name in ('analyze_pool_dbbench.py','analyze_sst_lifetime.py','analyze_maintenance_cover.py','audit_colocation_transactions.py'):
        shutil.copyfile(ROOT/name,output/'源码'/name)
    binary,headers=freeze_object(output,VERIFIED_DB)
    # 只复用本配置已采证的元数据首块差一次补齐路径；其他差异仍拒绝。
    if args.cohort_zero:
        require(not (output/'源码/元数据起点目标.json').exists(),'新放置禁止导入旧元数据目标')
        (output/'源码/启用本轮元数据参考').write_text('放置条件改变：由本轮p1真实观测建立参考，p2完整核验\n')
        (output/'源码/元数据参考首臂.txt').write_text('p1\n')
    else:
        metadata_source=IMAGE_DIR/'joint-controlled-sst-lifetime-v3-20261029-results/逐臂采证/p1/blocks.before.json'
        metadata=[r for r in json.loads(metadata_source.read_text()) if r['Zone']<3]
        require(len(metadata)==384 and all(r['RC']<1024 for r in metadata),'元数据参考范围异常')
        stamp(output/'源码/元数据起点目标.json',metadata)
        stamp(output/'源码/元数据目标来源.json',dict(来源=str(metadata_source),SHA256=digest(metadata_source),
            说明='本8GiB配置已完成p1观察；仅真实普通读补齐首块差一次，禁止降低RC或忽略完整起点'))
    if args.reuse_client:
        prior=args.reuse_client.resolve()
        require(prior.parent==IMAGE_DIR and prior!=output,'复用范围不符')
        saved=json.loads((prior/'状态.json').read_text());archived=json.loads((prior/'阶段归档.json').read_text())
        require(archived['完整'] and not archived['客机仍存活'] and saved['当前客机PID'] is None,
                '复用来源尚未归档退出')
        require(digest(Path(saved['镜像处理']['archive']))==saved['镜像处理']['archive_sha256'],'来源归档不同')
        old=prior/saved['标签'];identity=json.loads((old/'生命周期构建核验.json').read_text())
        require(identity['通过'] and identity['普通目录小测通过'] and
            digest(old/'db_bench')==identity['程序SHA256'] and
            digest(old/'fs_colocation.o')==identity['冻结ZenFS对象SHA256']==build['冻结ZenFS对象SHA256'] and
            digest(output/'源码/rrzc_sst_lifetime.h')==identity['计划头SHA256'] and
            digest(output/'源码/校准-db_bench_tool.cc')==identity['原客户端源码SHA256'],
            '来源程序、负载或冻结ZenFS字节不同')
        if args.sampling_scope:
            from sampling_scope_bundle import freeze as freeze_sampling
            freeze_sampling(output,prior,identity,build)
        else:
            programs={}
            for name in ('db_bench','space_probe'):
                programs[name]=digest(old/name);shutil.copyfile(old/name,output/'源码'/(name+'.frozen'))
            stamp(output/'源码/复用真实DB程序.json',dict(来源=str(prior),程序=programs,
                对象SHA256=identity['冻结ZenFS对象SHA256'],原库SHA256=build['原库SHA256'],
                生命周期客户端源码SHA256=identity['新客户端源码SHA256'],生命周期计划头SHA256=identity['计划头SHA256'],
                说明='逐字复用已完成实际业务和全库校验的客户端；本轮仍独立重复Greedy'))
    stamp(output/'预注册计划.json',dict(目的='仅检验真实SST生命周期是否创造联合机会，不预设正收益',平台=profile8(),
        顺序=['共同装载','p1-Greedy','p2-Greedy'],固定K=1,RR阈值=1024,原GC百分比=20,候选='original及既有紧急恢复',
        固定模型=headers,固定H=args.persistent_h,标签范围='窗口0' if args.cohort_zero else '六窗',
        采样范围修正=args.sampling_scope,采样诊断='未满区也采C4；候选资格及评分不改；新程序必须独立重复' if args.sampling_scope else '原采样范围',
        配对说明='H0加全部W0，其他普通分配；新布局须独立Greedy重复' if args.cohort_zero else '既有全六窗H/W',
        负载=('所有读批固定读取窗口0前4096个H键；' if args.persistent_h else '各窗口读取本窗前4096个H键；')+
            '六个65536键窗口，两次顺序覆盖其余61440个W键，再分8批读取H，每批8192次',
        总写=737280,总读=393216,成功请求=1130496,前台='单线程分阶段closed-loop，不设置QPS限制',
        显式Flush='每2048个W键Flush并等待正常后台Compaction；每个覆盖pass结束再次Flush排空；不主动CompactRange',
        维护检查='两个覆盖pass及八个读批次分别检查，共60次；不是原十秒周期并发性能',
        禁止='不外部删除SST、不改RR阈值、不强制victim，不根据维护结果改变请求',
        缓存='Block/Row/Persistent缓存关闭、Direct I/O、WAL关闭，MemTable保留',
        起点='相同装载及已核验回放；不是NAND快照，本轮放置及读取配置须重新核验Greedy重复',
        元数据补齐=('本轮p1实际建立新放置参考，p2严格核验；' if args.cohort_zero else '')+
            '复用已验证普通读补齐工具，0或1次真实读取；完整逐块门控不放宽',
        设备计数窗口='准备前计数单存strict.preparation.before.json；strict.before.json在准备与起点门控后重新采集',
        资源=dict(新增磁盘预算字节=16<<30,单台内存预算字节=14<<30)))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output,binary)
    finally:password=None


if __name__=='__main__':main()
