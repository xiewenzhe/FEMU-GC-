#!/usr/bin/env python3
"""复用同一临时盘，独立客户端固定键段装载，起始门控后串行两轮Greedy。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

from run_repeat_order_host import freeze
from run_rr_path_host import preflight,IMAGE_DIR,ROOT,RrRun
from run_all_blocks_followup_host import PARENT
from run_triggered_gc_host import wait_stage,stop_business,preserve_unverified_image
from reuse_temporary_run import ReuseRun
from run_ycsb_quick_host import profile8
from run_dbbench_parallel_host import stamp
from run_joint_host import digest_manifest
from ycsb_af import require,digest
from calibration_source import wrapper
from logical_input_guest import wrappers as logical_wrappers
from analyze_calibration import analyze,background_overlap
from run_ycsb_af_host import resource_sample

PREVIOUS=IMAGE_DIR/'joint-repeat-order-v6-20261026-results'
IMAGE=IMAGE_DIR/'joint-repeat-order-v2-20261026-repeat.qcow2'
MAX_BASELINE_BYTES=200 << 30


def check_baseline_budget(new_bytes, limit=MAX_BASELINE_BYTES):
    from result_storage import check_maintenance
    check_maintenance(IMAGE_DIR)
    used=int(subprocess.check_output(['du','-sB1',str(IMAGE_DIR)],text=True).split()[0])
    require(used+new_bytes<=limit,
            '项目实验目录达到空间上限：已占'+str(used)+'字节；请先整理已归档结果')
    return used


def business_modes(model_pair,unit_cost=False):
    pair=(('g1','greedy'),('g2','blocks' if model_pair else 'greedy'))
    return (('p1','greedy'),('p2','greedy'))+pair if unit_cost else pair


def validate_fresh_mode(args):
    """资源配置变化的基线可独立重建，不借用旧资源配置的收益准入。"""
    baseline = getattr(args, 'baseline_only', False)
    if baseline:
        require(args.fresh_image and args.reuse_client and args.matched_observation and
                not args.model_pair and not args.unit_cost and not args.reclaim_price and
                not args.space_goal and not args.repeat_acceptance and not args.only_prepare,
                '独立Greedy基线要求新登记镜像、同客户端及共同观测，禁止模型配对或旧配置准入')
    require(not args.fresh_image or (args.reuse_client and args.matched_observation and
        not args.cleanup_archived and
        (baseline or (args.model_pair and args.repeat_acceptance))),
        '新镜像仅用于明确的独立Greedy基线或已验收软件恢复的模型对照')


def verify_device_limits(run):
    """管理SSH使用PTY，其连接提示不能混入nvme的结构化输出。"""
    raw=run.target+'/设备资源查询.json'
    run.root('nvme zns id-ns /dev/nvme0n1 -o json > '+raw+
             ' 2> '+run.target+'/设备资源查询错误.log',30)
    identify=json.loads(run.ssh('cat '+raw))
    limits=run.profile['沿用条件']
    require(identify['mar']+1==limits['最大活动Zone数'] and
            identify['mor']+1==limits['最大打开Zone数'],
            '真实设备Open/Active与本轮配置不同，停止业务')
    result=dict(查询=identify,最大活动Zone数=identify['mar']+1,
                最大打开Zone数=identify['mor']+1)
    stamp(run.output/'真实设备资源上限.json',result)
    return result


def working_image(args):
    return IMAGE_DIR/(args.label+'-calibration.qcow2') if getattr(args,'fresh_image',False) else IMAGE


def detach_supervisor(output):
    """认证只在内存传给独立会话；长时监督不再依赖交互终端存活。"""
    log=output/'独立监督.log'
    require(not log.exists(),'不覆盖已有监督日志')
    ready_read,ready_write=os.pipe()
    pid=os.fork()
    if pid:
        os.close(ready_write)
        try:
            ready=os.read(ready_read,4096)
        finally:os.close(ready_read)
        require(ready==b'ready','独立监督启动未确认：'+ready.decode('utf-8',errors='replace'))
        print('独立监督PID='+str(pid),flush=True)
        return False
    os.close(ready_read)
    try:
        os.setsid()
        descriptor=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(descriptor,1);os.dup2(descriptor,2);os.close(descriptor)
        null=os.open('/dev/null',os.O_RDONLY);os.dup2(null,0);os.close(null)
        # 父进程收到就绪前不退出，避免PTY关闭先于子进程完成setsid。
        os.write(ready_write,b'ready')
    except BaseException as error:
        os.write(ready_write,('启动失败：'+repr(error)).encode('utf-8'))
        os._exit(1)
    finally:os.close(ready_write)
    return True


def reused_client_manifest(args):
    previous=IMAGE_DIR/(args.reuse_client+'-results')
    state=json.loads((previous/'状态.json').read_text())
    archive=json.loads((previous/'阶段归档.json').read_text())
    require(state['标签']==args.reuse_client and state['当前客机PID'] is None and
        archive['完整'] and not archive['客机仍存活'] and archive['业务成功'],
        '诊断客户端来源未成功归档退出')
    tar=previous/(args.reuse_client+'-calibration.tar.gz')
    require(digest(tar)==state['镜像处理']['archive_sha256'],'来源完整归档哈希不符')
    plan=json.loads((previous/'预注册计划.json').read_text())
    require(bool(plan.get('共同观测',False))==args.matched_observation,'共同观测配置与来源不同，拒绝混用二进制')
    wanted=dict(初始装载键数=args.load_keys,RC策略=args.initial_rc_policy,真实块计数准备=args.match_rc,
        内部随机性控制=args.fix_tls,前台固定全局顺序=args.front_order,固定逻辑输入=args.logical_input,
        共享单后台线程=args.single_background,每次写后排空后台=args.drain_background,
        逐请求跟踪=args.request_trace,设备稀疏事件=args.device_event_trace,
        Flush同步分段=args.flush_wait_trace,GC批次检查门=args.gc_gate,最大写缓冲数=args.write_buffers)
    if args.target_trace:
        wanted.update(辅助目录tmpfs诊断=args.aux_tmpfs,目标分配稀疏诊断=True,
            实际业务批次数=args.diagnostic_rounds,受控空间请求确认=args.synchronous_space)
    require(all(plan.get(k)==v for k,v in wanted.items()),'本轮控制配置与来源不同，拒绝冒称相同条件复测')
    root=previous/args.reuse_client
    files=('db_bench','校准客户端身份.json','原始-db_bench_tool.cc','校准-db_bench_tool.cc',
        '原始-flush_job.cc','诊断-flush_job.cc','Flush诊断身份.json','校准链接映射.txt',
        '原生Make计划.txt','Flush原生构建计划.txt','诊断-sync_point.cc','诊断-sync_point_impl.cc','同步点头文件.h')
    if args.target_trace:
        files+=('fs_all_blocks.o','目标采证身份.json','原始-zbd_zenfs.cc','目标采证-zbd_zenfs.cc')
        if args.gc_gate:files+=('边界受控-fs_zenfs.cc',)
        if args.synchronous_space:files+=('rrzc_boundary_sync.h',)
    if args.matched_observation:
        files+=('rrzc_direct_zenfs.inc','共同观测-原始选区.inc','设备摘要-zbdlib_zenfs.cc',
            'rrzc_io_trace.h','rrzc_front_order.h')
    require(all((root/name).is_file() for name in files),'来源诊断构建证据缺失')
    identity=json.loads((root/'校准客户端身份.json').read_text())
    require(digest(root/'db_bench')==identity['客户端SHA256'],'来源客户端与身份文件不一致')
    if args.target_trace:
        target_identity=json.loads((root/'目标采证身份.json').read_text())
        require(digest(root/'fs_all_blocks.o')==identity['ZenFS对象SHA256']==target_identity['组合对象SHA256'],
            '来源目标采证对象与客户端构建身份不同')
        require(target_identity['受控批次检查']==args.gc_gate and
            target_identity['受控空间请求确认']==args.synchronous_space,'来源对象调度身份不同')
        require(bool(target_identity.get('共同观测',False))==args.matched_observation,'来源对象共同观测身份不同')
    require(json.loads((root/'expected.json').read_text())==json.loads((PREVIOUS/'源码/expected.json').read_text()),
        '诊断客户端原软件身份与当前冻结来源不同')
    return dict(客机目录='/home/femu/'+args.reuse_client,文件SHA256={name:digest(root/name) for name in files},
        来源实验=args.reuse_client,来源归档SHA256=digest(tar),配置核验=wanted,
        打开线程运行参数=dict(来源=plan.get('打开文件线程数',16),本轮=args.file_opening_threads,
            说明='仅运行时参数，可复用同一二进制；线程数变化不能合并为同条件重复'))



def clean_archived_guest(run,label):
    archived_guest=IMAGE_DIR/(label+'-results')
    old=json.loads((archived_guest/'阶段归档.json').read_text())
    status=json.loads((archived_guest/'状态.json').read_text())
    require(status['标签']==label,'旧实验状态与指定标签不符')
    archive=Path(status['镜像处理']['archive'])
    require(archive.parent==archived_guest and archive.is_file(),'已登记归档不属于指定结果目录')
    require(old['完整'] and not old['客机仍存活'] and status['当前客机PID'] is None and
            digest(archive)==status['镜像处理']['archive_sha256'],
            '上一轮宿主归档未通过完整性核验，拒绝清理客机副本')
    run.record(阶段='宿主归档哈希已核验，清理客机中前轮冗余副本')
    recovered=(' '+status['镜像处理']['archive_sha256']) if archive.name.endswith('-calibration-recovered.tar.gz') else ''
    run.root(f'python3 {run.target}/cleanup_archived_guest.py {run.target} /home/femu/{status["标签"]}'+recovered,120)


class CalibrationRun(ReuseRun):
    skip_extracted_logical_input=True

    def start(self):
        if getattr(self,'fresh_image',False):return RrRun.start(self)
        return super().start()

    def command(self,argv,timeout=60):
        if getattr(self,'ftl_audit_binary',None) and argv and str(argv[0]).endswith('qemu-system-x86_64'):
            binary=Path(self.ftl_audit_binary)
            require(digest(binary)==self.ftl_audit_sha256,'FTL诊断二进制启动前身份变化')
            enabled=getattr(self,'ftl_audit_enabled',True)
            log=self.output/'FEMU设备命令.bin'
            require(not enabled or not log.exists(),'不覆盖FTL设备命令记录')
            self.record(设备命令日志=str(log) if enabled else None,完整设备命令追踪=enabled,
                        实际FEMU路径=str(binary),实际FEMUSHA256=self.ftl_audit_sha256)
            previous=os.environ.get('FEMU_FTL_AUDIT')
            if enabled:os.environ['FEMU_FTL_AUDIT']=str(log)
            else:os.environ.pop('FEMU_FTL_AUDIT',None)
            try:
                # 独立二进制离开原build目录，显式复用同一套BIOS/ROM资源。
                return super().command([str(binary),*argv[1:],'-L',str(ROOT.parents[1]/'pc-bios')],timeout)
            finally:
                if previous is None:os.environ.pop('FEMU_FTL_AUDIT',None)
                else:os.environ['FEMU_FTL_AUDIT']=previous
        if getattr(self,'device_event_trace',False) and argv and str(argv[0]).endswith('qemu-system-x86_64'):
            binary=ROOT.parents[1]/'build-femu/qemu-system-x86_64'
            require(b'FEMU_RR_EVENT_TRACE' in binary.read_bytes(),'当前FEMU二进制尚未编译设备事件日志，拒绝空采证')
            log=self.output/'FEMU设备事件.log'
            require(not log.exists(),'不覆盖设备事件日志')
            self.record(设备事件日志=str(log),设备事件时钟='宿主单调时钟；不得直接等同客机单调时钟')
            previous=os.environ.get('FEMU_RR_EVENT_TRACE')
            os.environ['FEMU_RR_EVENT_TRACE']='1'
            try:
                return super().command([*argv,'-D',str(log),'-d','guest_errors'],timeout)
            finally:
                if previous is None:os.environ.pop('FEMU_RR_EVENT_TRACE',None)
                else:os.environ['FEMU_RR_EVENT_TRACE']=previous
        return super().command(argv,timeout)

    def record(self,**values):
        super().record(**values)
        pid=self.state.get('当前客机PID')
        if pid and Path('/proc',str(pid)).exists():
            try:
                sample=resource_sample(pid)
                sample.update(宿主单调纳秒=time.monotonic_ns(),宿主负载=Path('/proc/loadavg').read_text().strip(),
                    宿主CPU累计=Path('/proc/stat').read_text().splitlines()[0],
                    阶段=self.state.get('阶段'),当前组=self.state.get('当前策略'))
                threads={}
                for p in Path('/proc',str(pid),'task').glob('*/schedstat'):
                    try:threads[p.parent.name]=p.read_text().strip()
                    except FileNotFoundError:threads[p.parent.name]='线程已退出'
                sample['线程调度累计']=threads
            except Exception as error:
                sample={'采样错误':repr(error),'PID':pid}
            with (self.output/'宿主资源.jsonl').open('a') as f:
                f.write(json.dumps(sample,ensure_ascii=False)+'\n')


def execute(args,password,output):
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.fresh_image=getattr(args,'fresh_image',False)
    run.device_event_trace=args.device_event_trace
    run.ftl_audit_binary=getattr(args,'ftl_audit_binary',None)
    if run.ftl_audit_binary:run.ftl_audit_sha256=digest(Path(run.ftl_audit_binary))
    run.args_reuse_image=working_image(args)
    run.profile=profile8()
    run.state.update(边界='独立装载和计时校准；两轮Greedy，不作模型收益结论',GC启用=True)
    if args.model_pair:
        run.state.update(边界='共同受控调度下Greedy及现有全块模型配对；非正常并发性能或正式收益',
            每臂策略=dict(business_modes(args.model_pair,args.unit_cost)),
            模型版本='RR空间替代计价V2' if args.reclaim_price else '单位回收V1' if args.unit_cost else '原全块时序模型')
    error=None;aux_started=False
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        if getattr(args, 'baseline_only', False):
            verify_device_limits(run)
        upload=[str(p) for p in (output/'源码').iterdir() if p.is_file()]
        run.command(run.scp()+upload+[f'femu@127.0.0.1:{run.target}/'])
        if args.cleanup_archived and args.cleanup_archived!=args.reuse_client:
            clean_archived_guest(run,args.cleanup_archived)
        run.record(阶段='核验原软件并复用共同ZenFS对象；客户端按显式构建或复用配置准备')
        if run.fresh_image:
            run.record(阶段='从宿主归档恢复同一客户端及历史源码，原库不重编')
            run.ssh(f'python3 {run.target}/restore_calibration_guest.py {run.target}',180)
        else:run.ssh(f'python3 {run.target}/reuse_build_guest.py {run.target}',120)
        if args.reuse_client and not run.fresh_image:
            run.record(阶段='核验并复用前轮诊断客户端，保持二进制一致，不重新编译')
            run.ssh(f'python3 {run.target}/reuse_calibration_client.py {run.target}',120)
        elif not args.reuse_client:
            run.ssh(f'python3 {run.target}/build_calibration_guest.py {run.target}',600)
        if args.unit_cost:
            run.record(阶段='独立重编单位回收成本对象及共同客户端，原模型和静态库不覆盖')
            run.ssh(f'python3 {run.target}/build_calibration_guest.py {run.target}',900)
        if getattr(args,'space_goal',False):
            run.record(阶段='仅链接共同空间收尾工具，业务程序和选区对象保持不变')
            run.ssh(f'python3 {run.target}/build_space_goal_guest.py {run.target}',180)
        if args.cleanup_archived and args.cleanup_archived==args.reuse_client:
            # 只有新目录内客户端复制与哈希核验成功后，才清理已归档来源的客机冗余副本。
            clean_archived_guest(run,args.cleanup_archived)
        run.command(run.scp()+[f'femu@127.0.0.1:{run.target}/校准客户端身份.json',str(output/'校准客户端身份.json')])
        run.record(阶段='客户端就绪，普通目录验证固定段装载与校验和')
        run.ssh(f'{run.target}/db_bench --db={run.target}/posix --num=8192 --value_size=4096 '
            '--benchmarks=rrzccalload,rrzctick,flush,rrzctick,waitforcompaction,rrzctick,readseq,verifychecksum '
            '--disable_wal=1 --compression_type=none --write_buffer_size=16777216 --threads=1 '
            f'> {run.target}/普通目录小测.log 2>&1',180)
        proof=run.ssh(f'cat {run.target}/普通目录小测.log')
        require(proof.count('校准装载段：')==4 and proof.count('校准阶段边界：')==3 and
                '8192 operations;' in proof and 'verifychecksum' in proof,'普通目录校准客户端验证未通过')
        run.ssh(f'{run.target}/db_bench --db={run.target}/posix --use_existing_db=1 --num={args.load_keys} '
            '--value_size=4096 --benchmarks=rrzctick,rrzclayout,rrzctick,rrzctrace '
            '--reads=786432 --writes=589824 --seed=20261023 --threads=1 '
            f'> {run.target}/请求序列核验.log 2>&1',120)
        trace=run.ssh(f'cat {run.target}/请求序列核验.log')
        rows=re.findall(r'校准请求摘要：操作=(READ|WRITE)，批次=(\d+)，累计=(\d+)，热点起点=(\d+)，键校验=(\d+)',trace)
        actual=sorted([[role,int(i),int(n),int(base),int(h)] for role,i,n,base,h in rows])
        if args.load_keys==1048576:
            prior=json.loads((PREVIOUS/'分析/独立核验.json').read_text())['各组']['g1']['批次请求摘要']
            require(actual==sorted(prior),'校准新增基准意外改变旧轮请求序列，拒绝开始设备业务')
            trace_check='与原1048576键负载的旧48批一致'
        else:
            second=run.ssh(f'{run.target}/db_bench --db={run.target}/posix --use_existing_db=1 --num={args.load_keys} '
                '--value_size=4096 --benchmarks=rrzctrace --reads=786432 --writes=589824 --seed=20261023 --threads=1',120)
            twice=sorted([[role,int(i),int(n),int(base),int(h)] for role,i,n,base,h in
                re.findall(r'校准请求摘要：操作=(READ|WRITE)，批次=(\d+)，累计=(\d+)，热点起点=(\d+)，键校验=(\d+)',second)])
            require(len(actual)==48 and twice==actual,'新容量请求序列两次生成不一致')
            trace_check='新容量下两次独立生成48批完全一致；与旧容量不混合'
        stamp(output/'请求序列核验.json',dict(通过=True,批次数=len(actual),依据=trace_check,边界='生成键序列摘要，非真实数据库IO'))
        if args.fix_tls:
            run.record(阶段='真实RocksDB短测：核对固定内部种子后的首批单内存表Flush边界')
            for tag in ('a','b'):
                common='--num=65536 --value_size=4096 --disable_wal=1 --compression_type=none '+\
                    f'--write_buffer_size=16777216 --max_write_buffer_number={args.write_buffers} --max_background_jobs=2 '+\
                    f'--file_opening_threads={args.file_opening_threads} '+\
                    '--target_file_size_base=16777216 --max_bytes_for_level_base=134217728 '+\
                    '--level0_file_num_compaction_trigger=2 --level0_slowdown_writes_trigger=4 '+\
                    '--level0_stop_writes_trigger=8 --seed=20261023 --threads=1'
                prefix=f'env RRZC_CAL_FLUSH_TRACE={int(args.flush_wait_trace)} RRZC_CAL_DRAIN_BACKGROUND={int(args.drain_background)} RRZC_CAL_SINGLE_BACKGROUND={int(args.single_background)} RRZC_CAL_TLS_SEED=1 RRZC_CAL_FRONT_ORDER={int(args.front_order)} RRZC_ROTATE_ROUNDS=4 {run.target}/db_bench --db={run.target}/posix/{tag} '
                run.ssh(prefix+common+' --benchmarks=rrzccalload '+f'> {run.target}/短测-{tag}-load.log 2>&1',180)
                run.ssh(prefix+common+' --use_existing_db=1 --benchmarks=rrzctick,rrzclayout,rrzctick,rrzcrotating,flush,waitforcompaction '+
                    f'--reads=131072 --writes=98304 > {run.target}/短测-{tag}-run.log 2>&1',180)
                run.ssh(f'cp {run.target}/posix/{tag}/LOG {run.target}/短测-{tag}.rocksdb.log')
            short={}
            for tag in ('a','b'):
                raw=run.ssh(f'cat {run.target}/短测-{tag}.rocksdb.log')
                if args.single_background:
                    proof=background_overlap(raw)
                    require(proof['重叠次数']==0 and all(proof['任务数'].values()),'普通目录后台任务仍重叠或样本缺失，停止ZNS业务')
                    stamp(output/f'后台短测-{tag}.json',proof)
                events=[json.loads(line.split('EVENT_LOG_v1 ',1)[1]) for line in raw.splitlines() if 'EVENT_LOG_v1 ' in line]
                sequence=[[e['num_memtables'],e['total_num_input_entries']] for e in events if e['event']=='flush_started']
                short[tag]=sequence if args.single_background else sequence[:8]
            require(len(short['a'])>=8 and short['a']==short['b'] and
                    (args.single_background or all(n==1 for n,_ in short['a'])),
                '短测前八批Flush边界或合并方式仍不同，不进入长测，先检查真实日志')
            stamp(output/'内部种子短测核验.json',dict(通过=True,Flush内存表数及条数=short,
                范围='完整Flush序列' if args.single_background else '前八批Flush',
                边界='共享单线程允许稳定的内存表合并；必须逐项一致且执行事件无重叠。不代表ZNS性能或完整后台顺序相同'))
        if args.logical_input:
            run.record(阶段='一次装载后保存固定逻辑输入，不保存NAND或RC快照')
            wait_stage(run,'source','run_logical_source_guest.sh',900,mode='greedy')
            if args.model_pair or args.matched_observation:
                proof=run.ssh(f'python3 {run.target}/cache_config_guard.py {run.target}/source/load.rocksdb.log {args.file_opening_threads}')
                stamp(output/'装载缓存核验.json',json.loads(proof))
        if args.aux_tmpfs:
            run.record(阶段='临时辅助盘诊断：保留fsync调用，仅隔离根ext4日志；不作持久性能结论')
            # setup即使在mount之后失败，也必须尝试归档和卸载。
            aux_started=True
            run.root(f'python3 {run.target}/aux_tmpfs_guest.py setup {run.target}',60)
        if getattr(args,'space_goal',False):
            run.record(阶段='真实模拟盘小测：在已捕获输入后的当前模拟盘验证挂载、采样与达标退出')
            wait_stage(run,'goal-smoke','run_space_goal_smoke_guest.sh',180,mode='greedy')
            smoke_rows=run.ssh(f'cat {run.target}/goal-smoke/goal.log')
            samples=[json.loads(line) for line in smoke_rows.splitlines() if line.startswith('{')]
            require(len(samples)==1 and samples[0]['序号']==0 and samples[0]['达标'],
                '真实空间工具最小测试未通过，不运行完整业务')
            smoke_log=run.ssh(f'cat {run.target}/goal-smoke/goal.zenfs.log')
            require('Filesystem mount OK' in smoke_log and
                '受控GC批次检查：启用=1，原启动水平=20%' in smoke_log,
                '空间工具小测维护日志缺失，不运行完整业务')
            stamp(output/'空间工具真实小测.json',dict(通过=True,采样=samples,
                边界='只验证真实挂载、空间采样和已达标退出；非完整维护路径或收益'))
        if args.match_rc and not args.target_trace:
            for tag in ('p1','p2'):
                run.record(阶段=tag+'仅验证初始逐块风险重建，不运行业务')
                wait_stage(run,tag,'run_prepare_only_guest.sh',600,mode='greedy')
            stamp(output/'逐块准备核验.json',dict(通过=True,边界='两轮仅装载及真实准备读通过，随后仍须重新构造业务起点'))
        for tag,mode in (() if args.only_prepare else business_modes(args.model_pair,args.unit_cost)):
            if args.flush_wait_trace and tag=='g1':
                run.root(f'nohup python3 {run.target}/flush_wait_observer.py {run.target} --seconds 1200 '
                    f'< /dev/null > {run.target}/Flush观察启动.log 2>&1 & echo $! > {run.target}/Flush观察.pid',30)
                observer=int(run.ssh(f'cat {run.target}/Flush观察.pid').strip())
                run.record(客机只读观察PID=observer,观察用途='长Flush阻塞栈采证，最多1200秒或g2结束自动退出',
                    观察清理='g2结束自动退出；异常由客机正常关机回收，日志随归档保留')
            run.record(阶段=tag+f'核验起点后运行{args.diagnostic_rounds}批请求',当前策略=tag,当前实际策略=mode)
            wait_stage(run,tag,'run_calibrated_guest.sh',2400,mode=mode)
            if args.model_pair or args.matched_observation:
                proof=run.ssh(f'python3 {run.target}/cache_config_guard.py {run.target}/{tag}/run.rocksdb.log {args.file_opening_threads}')
                stamp(output/(tag+'-缓存核验.json'),json.loads(proof))
            run.record(已完成=[*run.state.get('已完成',[]),tag])
            if args.unit_cost and tag in ('p1','p2'):
                from greedy_admission_archive import collect as collect_admission
                from analyze_controlled_pair import reference_guard
                admission=output/'新模型Greedy重复';admission.mkdir(exist_ok=True)
                collect_admission(run,admission,tag)
                if getattr(args,'space_goal',False):
                    from analyze_space_goal import arm as goal_arm
                    stamp(output/(tag+'-空间收尾核验.json'),goal_arm(admission/tag))
                if tag=='p2':
                    guard=reference_guard(admission/'p1',admission/'p2')
                    stamp(output/'新模型Greedy重复核验.json',guard)
                    require(guard['通过'],'新构建Greedy重复未通过，不进入模型配对')
                    if getattr(args,'space_goal',False):
                        from analyze_space_goal import repeat_guard as goal_repeat
                        goal_guard=goal_repeat(admission/'p1',admission/'p2')
                        stamp(output/'空间收尾Greedy重复核验.json',goal_guard)
                        require(goal_guard['通过'],'Greedy空间收尾未复现，不进入模型配对')
                    run.record(新构建Greedy重复通过=True)
            if args.model_pair and args.matched_observation and tag=='g1':
                from analyze_controlled_pair import reference_guard
                from greedy_admission_archive import collect as collect_admission
                admission=output/'Greedy准入采集';admission.mkdir()
                collect_admission(run,admission)
                donor=IMAGE_DIR/(args.reuse_client+'-results')
                reference=donor/args.reuse_client/'g1'
                guard=reference_guard(reference,admission/'g1')
                stamp(output/'模型运行前Greedy核验.json',guard)
                require(guard['通过'],'新环境Greedy未复现既有基线，停止模型臂，先保留首差')
                run.record(阶段='Greedy复现已通过，允许相同请求流运行现有模型',Greedy基线复现通过=True)
        expected=json.loads((output/'源码/expected.json').read_text())
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(expected),180))==expected,
                '校准改变原库、模型或原客户端源码')
        run.record(阶段='两轮校准完成，归档并正常关机')
    except BaseException as failed:
        error=failed;run.record(原始错误=repr(failed))
        if run.deployed:
            try:stop_business(run)
            except Exception as stopped:run.record(业务中断反馈=repr(stopped))
        raise
    finally:
        if aux_started:
            try:
                run.root(f'python3 {run.target}/aux_tmpfs_guest.py finish {run.target}',120)
                run.record(辅助诊断盘='内容归档哈希核验通过，已正常卸载')
            except Exception as aux_error:
                run.record(辅助盘收尾错误=repr(aux_error),辅助诊断盘='收尾未确认；客机仍须正常关机，不冒称内存内容已归档')
                if error is None:error=aux_error
        try:run.finish(error)
        except Exception as cleanup:
            retained=preserve_unverified_image(run,cleanup)
            if error is None and not retained:raise
            if error is not None:run.record(收尾另有错误=repr(cleanup))
    if error is not None:raise error
    if getattr(args,'ftl_audit_binary',None) and not args.unit_cost:
        from analyze_ftl_trace import analyze as analyze_ftl
        ftl=analyze_ftl(output/'FEMU设备命令.bin')
        stamp(output/'FTL命令首差.json',ftl)
        run.record(FTL设备核验=ftl)
    if args.model_pair:
        from analyze_controlled_pair import analyze as analyze_pair
        proof=analyze_pair(output,output/'受控配对分析')
        goal_proof=None
        if getattr(args,'space_goal',False):
            from analyze_space_goal import analyze as analyze_goal
            goal_proof=analyze_goal(output)
        run.record(阶段='Greedy及模型受控配对完成，缓存和请求已核验，已归档并正常关机',
            受控配对=proof,客机只读观察PID=None,
            共同空间目标=goal_proof,
            收益准入说明='单次受控探索配对；共同观测是否对齐以实际配置及配对核验为准，不得据此发布正常并发或统计稳定收益')
        return
    if args.target_trace:
        from analyze_target_repeat import analyze as analyze_targets
        proof=analyze_targets(output/args.label,output/'目标诊断分析',args.diagnostic_rounds)
        if args.matched_observation:
            from analyze_io_trace import compare
            io=compare(output/args.label/'g1',output/args.label/'g2')
            stamp(output/'设备读摘要首差.json',io)
            run.record(设备读摘要=io)
        if getattr(args,'baseline_only',False):
            from analyze_pool_baseline import analyze as analyze_baseline
            accepted=analyze_baseline(output)
            run.record(阶段='新资源配置两次Greedy核验结束，已归档并正常关机',
                协同池前置基线=accepted,客机只读观察PID=None)
            require(accepted['通过'],'新资源Greedy重复未通过；保留首差，不进入协同池收益实验')
            return
        run.record(阶段='目标分配与维护调度诊断完成，已归档并正常关机',
            目标诊断=proof,重复性工程预检通过=None,客机只读观察PID=None,
            收益准入说明='受控调度结果仅用于重复性核验；有限前缀不代表完整24批，均不能直接当作原并发模型收益')
        return
    if args.only_prepare:
        run.record(阶段='两轮初始风险重建核验完成，未运行业务，客机已退出')
    else:
        if args.flush_wait_trace:
            observer_log=output/args.label/'Flush阻塞采证.jsonl'
            events=[json.loads(line) for line in observer_log.read_text().splitlines()] if observer_log.exists() else []
            ended=bool(events and events[-1].get('事件')=='观察结束')
            run.record(客机只读观察PID=None,只读观察收尾='正常结束' if ended else '客机已退出，观察进程随客机回收；核对保留日志',
                观察结束证据=events[-1] if events else None)
        result=analyze(output/args.label,output/'分析')
        if args.flush_wait_trace:
            from analyze_flush_wait import analyze as analyze_wait
            waits=analyze_wait(output/args.label,output/'分析/Flush等待分段.json')
            run.record(Flush同步分段汇总=waits)
        quality=json.loads((output/'分析/重复性预检.json').read_text()) if args.fix_tls else None
        run.record(阶段='校准和独立核验完成，客机已退出',计时=result,
            重复性工程预检通过=quality['通过'] if quality else None,
            收益准入说明='同配置重复预检通过仍非统计显著性；逐请求/起点与物理放置差异须独立核验，不据此自动发布模型收益')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='joint-calibration-v3-20261026')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--baseline-only',action='store_true',help='新资源配置仅两次Greedy；新登记镜像复用已归档客户端，不运行模型')
    parser.add_argument('--detach',action='store_true',help='认证后在独立会话监督，日志落结果目录，避免聊天终端中断丢失监督')
    parser.add_argument('--model-pair',action='store_true',help='仅完整受控且复用同一客户端时，将第二臂启用现有全块模型；默认仍两轮Greedy')
    parser.add_argument('--unit-cost',action='store_true',help='独立重编单位回收净复制模型；先跑两次Greedy重复，通过后再配对，不修改旧模型')
    parser.add_argument('--reclaim-price',action='store_true',help='独立V2：预计RR复制收益扣除RR空间对当前缺口的替代估价；旧V1不变')
    parser.add_argument('--space-goal',action='store_true',help='独立诊断：业务后两组均按原Greedy补足1GiB可用空间，单独核对实际维护债务')
    parser.add_argument('--matched-observation',action='store_true',help='独立诊断对象：两臂共同计算模型建议，再分别执行；关闭逐块大日志并记录每请求设备读摘要')
    parser.add_argument('--file-opening-threads',type=int,choices=(1,16),default=16,
        help='仅运行时控制SST恢复预加载线程；默认保留16，1用于验证打开阶段设备读取交错')
    parser.add_argument('--initial-rc-policy',choices=('exact','low','matched'),default='exact')
    parser.add_argument('--fix-tls',action='store_true',help='仅实验客户端固定读写线程的内部随机数')
    parser.add_argument('--write-buffers',type=int,choices=(2,3),default=3,
        help='共同实验写缓冲数；2用于限制待落盘内存表合并造成的分化，原配置3仍保留')
    parser.add_argument('--match-rc',action='store_true',help='用真实普通读构造匹配的SST块RC，再等待近期窗口自然归零')
    parser.add_argument('--only-prepare',action='store_true',help='仅核验两次状态重建，不进行任何性能业务')
    parser.add_argument('--front-order',action='store_true',help='前台每4读3写固定全局顺序，不设置QPS或时间限速')
    parser.add_argument('--single-background',action='store_true',help='受控实验在打开后将Flush和Compaction放入共享单线程')
    parser.add_argument('--drain-background',action='store_true',help='每次Put后等待已触发后台任务完成；不主动Flush，不限QPS')
    parser.add_argument('--request-trace',action='store_true',help='仅固定全局顺序诊断时保存逐请求提交及完成记录，默认关闭')
    parser.add_argument('--device-event-trace',action='store_true',help='开启已编译的FEMU阈值与Reset稀疏事件；独立日志，默认关闭')
    parser.add_argument('--ftl-audit-binary',help='显式使用已验证的独立FTL诊断二进制，原FEMU不覆盖')
    parser.add_argument('--fresh-image',action='store_true',help='旧临时盘已回收时，从保留构建检查点创建登记临时盘并恢复宿主归档软件')
    parser.add_argument('--repeat-acceptance',type=Path,help='模型配对引用已完成的四次受控重复验收')
    parser.add_argument('--flush-wait-trace',action='store_true',help='独立Flush对象只记录日志刷新、目录同步及数据库锁分段；原库不变')
    parser.add_argument('--reuse-client',metavar='前轮实验标签',help='哈希核验后复用已归档前轮诊断客户端，不重新编译')
    parser.add_argument('--aux-tmpfs',action='store_true',help='仅诊断时隔离辅助目录与根ext4日志；不代表持久性能，默认关闭')
    parser.add_argument('--target-trace',action='store_true',help='独立重编ZenFS对象，仅添加迁移目标原扫描采证，不改变分配规则')
    parser.add_argument('--diagnostic-rounds',type=int,choices=(16,24),default=24,
        help='目标诊断可只跑原请求流前16批；不是完整24批收益实验')
    parser.add_argument('--synchronous-space',action='store_true',help='仅批次受控模式：原分配重试失败后请求并等待一次维护，不按墙钟轮询触发紧急GC')
    parser.add_argument('--logical-input',action='store_true',help='仅装载一次，再以同一串行NVMe写顺序重建每轮逻辑输入')
    parser.add_argument('--gc-gate',action='store_true',help='实验时将原有20%GC资格检查对齐到相同请求批次')
    parser.add_argument('--load-keys',type=int,choices=(1048576,917504),default=1048576,
        help='初始装载键数；917504键=3.5GiB，只用于原异步维护的容量敏感性检验')
    parser.add_argument('--cleanup-archived',metavar='旧实验标签',help='哈希核验后回收指定旧实验在客机内的冗余副本')
    args=parser.parse_args();require(re.fullmatch(r'joint-[a-z0-9-]+',args.label),'非法标签')
    require(not args.unit_cost or (args.model_pair and args.fresh_image and args.matched_observation),
        '单位回收模型只支持新登记镜像中的共同构建、Greedy重复和完整配对')
    require(not args.reclaim_price or args.unit_cost,'RR空间替代估价要求显式单位回收模型')
    require(not args.space_goal or args.unit_cost,'共同空间收尾只允许完整受控单位回收模型配对')
    acceptance=None
    if args.repeat_acceptance:
        require(args.model_pair and args.matched_observation,'重复验收仅用于共同观测模型对照')
        proof=args.repeat_acceptance.resolve()
        require(proof.is_relative_to(ROOT/'results') and proof.name=='验收结论.json','验收路径必须属于项目结果目录')
        acceptance=json.loads(proof.read_text())
        accepted_state=json.loads(proof.with_name('状态.json').read_text())
        require(acceptance['验收通过'] and accepted_state['验收通过'] and
            Path(accepted_state['本轮'])==IMAGE_DIR/(args.reuse_client+'-results'),'验收未通过或不对应来源实验')
        args.repeat_acceptance=proof
    validate_fresh_mode(args)
    audit_identity=None
    if args.ftl_audit_binary:
        binary=Path(args.ftl_audit_binary).resolve()
        require(binary.name=='qemu-system-x86_64' and binary.is_relative_to(ROOT/'results') and binary.is_file(),
            'FTL诊断程序必须属于项目结果目录')
        proof=binary.parent.parent/'构建核验.json'
        require(proof.is_file(),'FTL诊断缺少独立构建核验')
        audit_identity=json.loads(proof.read_text())
        require(audit_identity['原样重链接完全相同'] and audit_identity['无日志FTL代码段相同'] and
            audit_identity['诊断二进制SHA256']==digest(binary) and
            audit_identity['原有身份']['二进制']==digest(ROOT.parents[1]/'build-femu/qemu-system-x86_64'),
            'FTL诊断或原二进制身份不符')
        require(args.matched_observation and args.file_opening_threads==1 and not args.device_event_trace,
            'FTL诊断仅用于单线程恢复的共同观测重复，不能混用旧事件开关')
        args.ftl_audit_binary=str(binary)
        if acceptance:
            accepted_plan=json.loads((IMAGE_DIR/(args.reuse_client+'-results')/'预注册计划.json').read_text())
            require(audit_identity==accepted_plan.get('FTL诊断身份'),'模型对照设备程序与已验收版本不同')
    boundary_control=(args.target_trace and args.gc_gate and args.diagnostic_rounds in (16,24) and
        args.load_keys==917504 and args.drain_background)
    require(not args.synchronous_space or boundary_control,'空间交接只允许明确的受控调度诊断')
    require(not args.matched_observation or (boundary_control and args.synchronous_space and
        args.logical_input and args.request_trace and args.single_background and
        args.diagnostic_rounds==24 and args.flush_wait_trace),'共同观测只允许完整受控诊断')
    require(not args.model_pair or (boundary_control and args.synchronous_space and args.reuse_client and
        args.logical_input and args.request_trace and args.single_background and
        args.diagnostic_rounds==24 and not args.only_prepare),'模型配对只允许完整已核验受控配置及同二进制复用')
    require(not args.cleanup_archived or (re.fullmatch(r'joint-controlled-[a-z0-9-]+',args.cleanup_archived)
        and args.cleanup_archived!=args.label),'旧实验标签非法或与新实验相同')
    require(not args.cleanup_archived or args.label.startswith('joint-controlled-'),
        '客机冗余副本清理要求当前实验也使用joint-controlled-标签；启动前拒绝不兼容命名')
    require(not args.reuse_client or (re.fullmatch(r'joint-controlled-[a-z0-9-]+',args.reuse_client)
        and args.reuse_client!=args.label),
        '复用客户端来源非法或等于当前目录')
    require(not args.reuse_client or args.flush_wait_trace,'诊断客户端复用目前只支持相同Flush分段配置')
    require(not args.target_trace or (args.aux_tmpfs and
        (not args.gc_gate or boundary_control)),
        '目标采证要求独立对象；边界调度只允许明确的3.5GiB输入、写后排空受控诊断')
    require(args.diagnostic_rounds==24 or args.target_trace,'有限前缀仅用于明确的目标采证')
    require(not args.aux_tmpfs or (args.logical_input and args.flush_wait_trace and
        args.label.startswith('joint-controlled-')),'辅助内存盘仅支持固定输入的受控Flush诊断')
    require(args.match_rc==(args.initial_rc_policy=='matched'),'真实准备读取和matched准入必须同时启用')
    require(not args.only_prepare or args.match_rc,'仅准备模式要求真实块计数重建')
    require(not args.logical_input or (args.match_rc and args.front_order and args.fix_tls),'固定逻辑输入要求相同RC、全局顺序及内部种子')
    require(not args.gc_gate or (args.logical_input and args.front_order),'GC批次检查门要求固定输入和全局顺序')
    require(not args.single_background or (args.fix_tls and args.front_order and (not args.gc_gate or boundary_control)),
        '共享后台与GC边界调度只允许明确的诊断配置；普通入口保持原约束')
    require(not args.drain_background or args.single_background,'后台排空要求已验证的共享单线程')
    require(not args.request_trace or args.front_order,'逐请求跟踪要求前台固定全局顺序')
    require(not args.flush_wait_trace or args.drain_background,'Flush等待诊断先沿用写后排空受控配置')
    require(not args.gc_gate or args.load_keys==1048576 or boundary_control,
        '边界检查只在显式目标采证及写后排空配置中与新容量组合')
    budget=preflight();extra=(16 if args.fresh_image else 8) if args.logical_input else 2
    require(budget['data可用字节']>=(26+extra)<<30,'原保护余量及明确登记的校准预算不足')
    budget['新增磁盘预算字节']=extra<<30
    budget['项目目录已占字节']=check_baseline_budget(extra<<30)
    budget['项目目录上限字节']=MAX_BASELINE_BYTES
    output=IMAGE_DIR/(args.label+'-results');require(not output.exists(),'不覆盖旧结果')
    require((args.fresh_image or IMAGE.is_file()) and (PREVIOUS/'阶段归档.json').is_file(),'复用盘或前轮归档不存在')
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    reused=reused_client_manifest(args) if args.reuse_client else None
    password=getpass.getpass('校准实验认证（仅内存）：')
    output.mkdir();freeze(output,restore_fix=True,reuse_build=PREVIOUS)
    if args.fresh_image:
        from calibration_restore_bundle import freeze_bundle
        freeze_bundle(output,PREVIOUS,IMAGE_DIR/(args.reuse_client+'-results'),reused,
                      metadata_reference=not args.baseline_only)
        if args.baseline_only:
            (output/'源码/启用本轮元数据参考').write_text(
                '新Open/Active配置：g1记录本轮元数据参考，g2严格核验；不导入旧配置RC\n')
        for name in ('calibration_restore_bundle.py','restore_calibration_guest.py','metadata_start_guest.py'):
            shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    if audit_identity:
        stamp(output/'FTL独立构建身份.json',audit_identity)
        shutil.copyfile(ROOT/'analyze_ftl_trace.py',output/'源码/analyze_ftl_trace.py')
        for name in ('build_ftl_audit.py','rrzc_ftl_trace.h'):
            shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    for name in ('run_calibration_host.py','calibration_source.py','calibration_guest.py','build_calibration_guest.py',
                 'rrzc_tls_seed.h','tls_seed_probe.cc','rrzc_front_order.h','logical_input_guest.py',
                 'controlled_gc_source.py','build_controlled_gc_guest.py'):
        shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    if args.cleanup_archived:shutil.copyfile(ROOT/'tests/cleanup_archived_guest.py',output/'源码/cleanup_archived_guest.py')
    if args.flush_wait_trace:
        shutil.copyfile(ROOT/'tests/flush_wait_source.py',output/'源码/flush_wait_source.py')
        shutil.copyfile(ROOT/'analyze_flush_wait.py',output/'源码/analyze_flush_wait.py')
        shutil.copyfile(ROOT/'tests/flush_wait_observer.py',output/'源码/flush_wait_observer.py')
        (output/'源码/启用Flush分段').write_text('仅独立实验客户端记录真实同步调用前后时点，原库不变\n')
    if reused:
        shutil.copyfile(ROOT/'tests/reuse_calibration_client.py',output/'源码/reuse_calibration_client.py')
        stamp(output/'源码/复用诊断客户端.json',reused)
    if args.model_pair:
        shutil.copyfile(ROOT/'tests/greedy_admission_archive.py',output/'源码/greedy_admission_archive.py')
        shutil.copyfile(ROOT/'tests/cache_config_guard.py',output/'源码/cache_config_guard.py')
        shutil.copyfile(ROOT/'analyze_controlled_pair.py',output/'源码/analyze_controlled_pair.py')
    if args.matched_observation:
        for name in ('matched_observation_source.py','rrzc_io_trace.h','cache_config_guard.py'):
            shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
        from matched_observation_source import frontend
        (output/'源码/rrzc_front_order.h').write_text(frontend((ROOT/'tests/rrzc_front_order.h').read_text()))
        (output/'源码/启用共同观测').write_text('两臂共同查询和计算；Greedy仅在最终执行点按原排名选择，不固定victim；逐块大日志关闭\n')
        shutil.copyfile(ROOT/'analyze_io_trace.py',output/'源码/analyze_io_trace.py')
    if args.unit_cost:
        for name in ('unit_cost_source.py',):
            shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
        shutil.copyfile(ROOT/'model/rrzc_unit_cost_selector.h',output/'源码/rrzc_unit_cost_selector.h')
        (output/'源码/启用单位回收成本').write_text('独立新对象；两次Greedy先验收，再运行Greedy与模型，共同计算，不构造补充区计划\n')
    if args.space_goal:
        for name in ('space_goal_device.cc','space_goal_source.py','build_space_goal_guest.py'):
            shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
        shutil.copyfile(ROOT/'analyze_space_goal.py',output/'源码/analyze_space_goal.py')
    if args.reclaim_price:
        shutil.copyfile(ROOT/'model/rrzc_reclaim_price_selector.h',output/'源码/rrzc_reclaim_price_selector.h')
        (output/'源码/启用RR空间替代成本').write_text('计入取消RR后对当前空间缺口的替代复制估价；不改变实际回收和RR路径\n')
    if args.aux_tmpfs:
        shutil.copyfile(ROOT/'tests/aux_tmpfs_guest.py',output/'源码/aux_tmpfs_guest.py')
        (output/'源码/启用辅助目录tmpfs').write_text('诊断根ext4 journal干扰，保留fsync调用；不得作为持久性能收益实验\n')
    if args.target_trace:
        for name in ('target_alloc_source.py','build_target_alloc_guest.py'):
            shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
        shutil.copyfile(ROOT/'analyze_target_repeat.py',output/'源码/analyze_target_repeat.py')
        (output/'源码/启用目标分配采证').write_text('仅增加日志，不改变选择规则，原库/源文件保持不变\n')
    if args.synchronous_space:
        shutil.copyfile(ROOT/'tests/rrzc_boundary_sync.h',output/'源码/rrzc_boundary_sync.h')
        (output/'源码/启用受控空间交接').write_text('原重试失败后才请求一次维护；整轮完成确认后再重试，不预设任何选择\n')
    shutil.copyfile(ROOT/'analyze_calibration.py',output/'源码/analyze_calibration.py')
    guest=wrapper((ROOT/'tests/run_all_blocks_dbbench_guest.sh').read_text())
    guest=guest.replace('"$target/g1" > "$result/起始核验.json"',
        '"$target/g1" '+args.initial_rc_policy+' > "$result/起始核验.json"',1)
    guest=guest.replace('RRZC_CAL_LOAD_CHUNK=2048','RRZC_CAL_LOAD_CHUNK=2048 RRZC_CAL_TLS_SEED='+str(int(args.fix_tls)),1)
    guest=guest.replace('RRZC_CAL_LOAD_CHUNK=2048','RRZC_CAL_LOAD_CHUNK=2048 RRZC_CAL_FRONT_ORDER='+str(int(args.front_order)),1)
    guest=guest.replace('RRZC_CAL_LOAD_CHUNK=2048','RRZC_CAL_LOAD_CHUNK=2048 RRZC_CAL_SINGLE_BACKGROUND='+str(int(args.single_background)),1)
    guest=guest.replace('RRZC_CAL_LOAD_CHUNK=2048','RRZC_CAL_LOAD_CHUNK=2048 RRZC_CAL_DRAIN_BACKGROUND='+str(int(args.drain_background)),1)
    guest=guest.replace('RRZC_CAL_LOAD_CHUNK=2048','RRZC_CAL_LOAD_CHUNK=2048 RRZC_CAL_FLUSH_TRACE='+str(int(args.flush_wait_trace)),1)
    if args.request_trace:
        guest=guest.replace('RRZC_CAL_LOAD_CHUNK=2048',
            'RRZC_CAL_LOAD_CHUNK=2048 RRZC_CAL_REQUEST_TRACE="${request_trace:-}"',1)
        guest=guest.replace('hotspot=1;phase run','request_trace="$result/请求提交.bin"\nhotspot=1;phase run',1)
        guest=guest.replace('hotspot=0;phase verify','request_trace=""\nhotspot=0;phase verify',1)
    if args.gc_gate:
        (output/'源码/启用受控GC检查').write_text('仅该实验显式启用；原GC启动20%与选区判断保持不变\n')
        require(guest.count('enabled=0;greedy=1;planning=()')==1 and
                guest.count('RRZC_CAL_FRONT_ORDER=1')==1 and guest.count('hotspot=1;phase run')==1,
                '受控GC包装器锚点不唯一')
        guest=guest.replace('enabled=0;greedy=1;planning=()', 'gc_gate=""\nenabled=0;greedy=1;planning=()',1)
        guest=guest.replace('RRZC_CAL_FRONT_ORDER=1',
            'RRZC_CAL_FRONT_ORDER=1 RRZC_CAL_GC_GATE="$gc_gate" ZENFS_RRZC_CAL_GC_GATE="$gc_gate"',1)
        guest=guest.replace('hotspot=1;phase run','gc_gate="$result/受控GC"\nhotspot=1;phase run',1)
    guest=guest.replace('--max_write_buffer_number=3','--max_write_buffer_number='+str(args.write_buffers),1)
    require(guest.count('--max_background_jobs=2')==1,'打开线程参数锚点不唯一')
    guest=guest.replace('--max_background_jobs=2','--max_background_jobs=2 --file_opening_threads='+str(args.file_opening_threads),1)
    if args.load_keys!=1048576:
        require(guest.count('--num=1048576')==1,'新容量装载锚点不唯一')
        guest=guest.replace('--num=1048576','--num='+str(args.load_keys),1)
        require(guest.count('--reads=1048576 --writes=0')==1,'新容量完整读回锚点不唯一')
        guest=guest.replace('--reads=1048576 --writes=0',f'--reads={args.load_keys} --writes=0',1)
    if args.match_rc:
        guest=guest.replace('python3 "$target/calibration_guest.py" blocks',
            'python3 "$target/calibration_guest.py" warmup "$result" > "$result/真实准备读取.log" 2>&1\n'
            'python3 "$target/calibration_guest.py" blocks',1)
    if args.logical_input:
        source,guest=logical_wrappers(guest)
        (output/'源码/run_logical_source_guest.sh').write_text(source)
    if args.target_trace:
        require(guest.count('ZENFS_MAINT_DIAG=1')==1,'环境锚点不唯一')
        guest=guest.replace('ZENFS_MAINT_DIAG=1','RRZC_CAL_TARGET_TRACE=1 ZENFS_MAINT_DIAG=1',1)
        if args.diagnostic_rounds==16:
            require(guest.count('RRZC_ROTATE_ROUNDS=24')==1 and guest.count('--reads=786432 --writes=589824')==1,
                '有限前缀锚点不唯一')
            guest=guest.replace('RRZC_ROTATE_ROUNDS=24','RRZC_ROTATE_ROUNDS=16',1)
            guest=guest.replace('--reads=786432 --writes=589824','--reads=524288 --writes=393216',1)
    if args.matched_observation:
        from matched_observation_source import wrapper as matched_wrapper
        guest=matched_wrapper(guest)
    if args.unit_cost:
        from unit_cost_source import wrapper as unit_wrapper
        guest=unit_wrapper(guest)
    if args.space_goal:
        from space_goal_source import wrapper as goal_wrapper,smoke as goal_smoke
        guest=goal_wrapper(guest)
        (output/'源码/run_space_goal_smoke_guest.sh').write_text(goal_smoke(guest))
    if args.match_rc:
        prefix=guest.split('hotspot=1;phase run',1)[0]
        prefix=prefix.replace('"$target/g1" matched','"$target/p1" matched',1)
        (output/'源码/run_prepare_only_guest.sh').write_text(prefix+'printf "done\\n" > "$result/phase.txt"\n')
    (output/'源码/run_calibrated_guest.sh').write_text(guest)
    stamp(output/'预注册计划.json',dict(目的='共同受控环境下验证现有模型选区与真实维护结果' if args.model_pair else '校准起始装载和分阶段计时，不预设波动缩小',
        仅Greedy新配置基线=args.baseline_only,
        本轮设备配置=profile8(),
        模型配对=args.model_pair,每臂策略=dict(g1='greedy',g2='blocks' if args.model_pair else 'greedy'),
        共同观测=args.matched_observation,
        单位回收成本=args.unit_cost,
        RR空间替代计价=args.reclaim_price,
        共同空间目标诊断=args.space_goal,
        共同空间目标字节=(1<<30) if args.space_goal else None,
        空间收尾边界='业务后重新挂载，两组共同Greedy且无前台请求；只请求原GC20%检查，严格RR1024保留；共同最低1GiB，不冒称终态完全一致，单独报告超额空间与成本' if args.space_goal else None,
        实际执行组=dict(business_modes(args.model_pair,args.unit_cost)),
        新建临时镜像恢复=args.fresh_image,重复验收=str(args.repeat_acceptance) if args.repeat_acceptance else None,
        设备命令诊断=bool(audit_identity),FTL诊断身份=audit_identity,
        打开文件线程数=args.file_opening_threads,
        共同观测边界='两臂先运行同一原模型、共同查询、历史与安全复核；最终执行分别为原Greedy排名或模型推荐，逐块大日志都关闭。每请求设备读摘要仅定位，不证明完整NAND状态相同。' if args.matched_observation else None,
        缓存核验='两臂cache_size=0、row_cache_size=0、Direct I/O；实际打开日志须通过' if args.model_pair else None,
        观测差异=('两臂查询、计算及逐块日志开关相同，只在最终执行点切换原Greedy与模型；单次受控配对不能代表正常并发收益'
            if args.matched_observation else '模型额外C4查询和逐块诊断未对齐，不能直接归因选区收益') if args.model_pair else None,
        平台='8GiB，RR1024，GC20%，无WAL，不限速，单客机串行',
        业务=f'初始装载{args.load_keys}键×4KiB、{args.diagnostic_rounds*32768}读及{args.diagnostic_rounds*24576}写、{args.diagnostic_rounds}批次；每批热点规则不变，新容量与旧结果不混合',
        初始装载键数=args.load_keys,
        装载='2048键一段，显式Flush并WaitForCompact；与历史fillseq不同，不合并统计',
        门控='起始键范围、页映射及数据区空间严格相同；RC策略显式登记，不通过则停止业务并归档',
        RC策略=args.initial_rc_policy,低风险RC上限=1024//100 if args.initial_rc_policy=='low' else None,
        真实块计数准备=args.match_rc,准备目标RC=32 if args.match_rc else None,
        准备边界='通过普通读增加到32；未编程块保留0；静置31秒，精确核验SST块计数和近期窗口，不写设备计数',
        RC边界='low不声称逐块RC完全相同；保留会话、Reset代际、逐块RC和最大差，不人工改计数',
        内部随机性控制=args.fix_tls,
        前台固定全局顺序=args.front_order,固定逻辑输入=args.logical_input,
        共享单后台线程=args.single_background,
        每次写后排空后台=args.drain_background,
        逐请求跟踪=args.request_trace,
        设备稀疏事件=args.device_event_trace,
        Flush同步分段=args.flush_wait_trace,
        复用诊断客户端=args.reuse_client,
        辅助目录tmpfs诊断=args.aux_tmpfs,
        目标分配稀疏诊断=args.target_trace,实际业务批次数=args.diagnostic_rounds,
        受控空间请求确认=args.synchronous_space,
        有限前缀边界='16批为原24批请求前缀；减少终点，不改变每批请求或维护调度，仅用于首差诊断' if args.diagnostic_rounds==16 else None,
        辅助目录边界='tmpfs限额256MiB，原fsync调用保留；收尾复制哈希核验后卸载；不同于持久辅助目录，不作性能收益结论' if args.aux_tmpfs else '原客机根ext4辅助目录',
        重复客户端SHA256=reused['文件SHA256']['db_bench'] if reused else None,
        请求跟踪边界='RRZCREQ1头及每条七个小端uint64；成功API后记录完成，排空后记录放行；仅诊断，不代表无观测性能。' if args.request_trace else None,
        后台排空边界='等待已触发Flush/Compaction完成，不主动Flush；改变前后台重叠，仅用于受控基线。单独报告排空等待，API延迟不含该等待。' if args.drain_background else None,
        后台调度边界='只在独立客户端打开后等待已有后台任务完成，再将HIGH置0、LOW置1；原GC/RR调度保留。实际Flush/Compaction事件必须无重叠。' if args.single_background else None,
        GC批次检查门=args.gc_gate,
        GC批次检查边界=('仅受控实验每批请求后检查；原分配重试失败后按序请求维护，整轮结束确认，不再轮询瞬时等待计数。原资格与选区不变，同线程后台RR检查时机也改变，不代表原并发性能。'
            if args.synchronous_space else '仅受控实验把原GC资格检查从每10秒改为每批请求后一次；真实Zone分配等待时至多每秒额外检查一次。原空闲20%判断、候选、迁移及RR阈值不改。该调度不能代表原并发性能。') if args.gc_gate else None,
        全局顺序边界='每4读3写，实际API不重叠；无QPS或sleep限速，后台维护与压缩保留；不能代表原并发吞吐',
        固定输入边界='同一逻辑字节及辅助文件、同一串行NVMe写顺序；真实读取重建RC并核对块访问样本；不是NAND快照',
        最大写缓冲数=args.write_buffers,
        写缓冲边界='两组同配置；2改变原3缓冲的容量与合并机会，新旧性能不合并，不改变RR或GC规则',
        内部随机性边界='只固定实验读写线程的RocksDB TLS种子，不消耗请求PRNG；原库和后台调度不改',
        初步重复性门槛='两轮请求窗口较大值/较小值不超过1.05，且总迁移同口径差不超过5%；不构成统计显著性',
        计时='打开、布局、请求、flush、等待压缩和关闭分别计时',
        观测='Greedy及现有模型使用共同跟踪；模型自身C4查询和计算另列为机制成本' if args.model_pair else '本轮仅相同配置Greedy；模型对照的采样和日志统一仍需后续核验',
        镜像='从保留构建检查点创建一份登记临时盘，归档退出后回收，不作为新父镜像' if args.fresh_image else '复用同一已登记临时盘，不新增镜像或父链',资源=budget))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output):return
    try:execute(args,password,output)
    finally:password=None


if __name__=='__main__':main()
