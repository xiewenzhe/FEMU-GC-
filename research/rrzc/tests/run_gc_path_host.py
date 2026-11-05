#!/usr/bin/env python3
"""实验2：独立客机关闭RR，串行采集一次完整GC路径并归档关机。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import resource
import shlex
import shutil
import signal
import socket
import subprocess
import time
from types import SimpleNamespace

from run_dbbench_parallel_host import DbRun, ROOT, IMAGE_DIR, capacity, stamp
from run_joint_host import PROJECT, OLD_DEVICE, digest_manifest
from run_tail_ablation_fixed_pair import running_parent
from run_ycsb_quick_host import disk_space
from finerr_profile import settings
from ycsb_af import digest, require
from gc_path_trace import make_patch, analyze
from analyze_joint_opportunity import model_decisions

LABEL='joint-gc-path-v1-20261025'
PARENT=IMAGE_DIR/'joint-dbbench8-rotating-v9-20261024-prepare.qcow2'
SOURCE=IMAGE_DIR/'joint-dbbench8-rotating-v9-20261024-results'
INSPECT=IMAGE_DIR/'joint-rr-coverage-inspect-v1-20261024-results/源码'


class GcRun(DbRun):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.state.update(边界='纯GC路径教学实验；RR执行关闭；单台串行，不作性能收益对照',
            设备严格RR=False,主机同步RR=False,后台RR=False)

    def command(self, argv, timeout=60):
        if argv and str(argv[0]).endswith('qemu-system-x86_64'):
            require(argv.count(OLD_DEVICE)==1,'设备接线标记不符')
            argv=[settings(self.profile)['设备参数']+',zns_rr_strict=off' if x==OLD_DEVICE else x for x in argv]
            def unlocked():resource.setrlimit(resource.RLIMIT_MEMLOCK,(0,0))
            reply=subprocess.run(argv,cwd=PROJECT,text=True,stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,timeout=180,preexec_fn=unlocked)
            with (self.output/'管理命令.log').open('a') as stream:
                stream.write(json.dumps(argv,ensure_ascii=False)+'\n'+reply.stdout+'\n')
            require(reply.returncode==0,'独立GC客机启动失败')
            return reply.stdout
        return super().command(argv,timeout)

    def finish(self, error=None):
        errors=[]
        archive,expected=None,None
        try:
            if self.deployed:
                reference=getattr(self,'archive_input_reference',None)
                if reference:
                    require(digest(Path(reference['archive']))==reference['sha256'],
                            '共享输入来源变化，保留本轮镜像等待采证')
                remote=self.target+'-'+self.tag+'.tar.gz'
                extra=''.join(' --exclude='+shlex.quote(Path(self.target).name+'/'+pattern)
                              for pattern in getattr(self,'archive_exclusions',()))
                self.ssh(f'test ! -e {remote} && tar --exclude="{Path(self.target).name}/posix" '
                    f'{extra} -czf {remote} -C /home/femu {Path(self.target).name}',180)
                expected=self.ssh('sha256sum '+remote).split()[0]
                self.command(self.scp()+[f'femu@127.0.0.1:{remote}',str(self.output)],180)
                require(digest(self.output/Path(remote).name)==expected,'GC归档包哈希不符')
                excludes=['--exclude=*/逻辑输入/*.bin.gz'] if getattr(self,'skip_extracted_logical_input',False) else []
                self.command(['tar',*excludes,'-xzf',str(self.output/Path(remote).name),'-C',str(self.output)],120)
                archive=self.output/Path(remote).name
        except Exception as failure:errors.append('归档：'+str(failure))
        try:self.close_guest()
        except Exception as failure:errors.append('正常关机：'+str(failure))
        pid=self.state.get('当前客机PID')
        alive=bool(pid and Path('/proc',str(pid)).exists())
        if not alive:
            self.record(当前客机PID=None,当前镜像=None,阶段='GC已归档并退出' if not errors else '归档异常但客机已退出')
            if self.socket:
                reply=subprocess.run(['ssh','-S',self.socket,'-O','exit','-p',self.port,
                    'femu@127.0.0.1'],capture_output=True,text=True,timeout=15)
                (self.output/'SSH收尾.log').write_text(reply.stdout+reply.stderr)
        stamp(self.output/'阶段归档.json',dict(完整=not errors,错误=errors,客机仍存活=alive,
            业务成功=error is None,原始错误=None if error is None else repr(error)))
        self.finish_image(archive,expected,archived=not errors,guest_exited=not alive)
        if getattr(self,'archive_input_reference',None) and archive is not None and not errors:
            self.state['镜像处理']['archive_input_reference']=self.archive_input_reference
            self.record(镜像处理=self.state['镜像处理'])
        require(not errors and not alive,'GC归档或正常关机未通过：'+str(errors))


def preflight():
    require(PARENT.is_file() and not running_parent(PARENT),'软件父镜像不可用')
    listing=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
    require(str(IMAGE_DIR)+'/' not in listing,'已有本项目客机，停止叠加')
    with socket.socket() as sock:require(sock.connect_ex(('127.0.0.1',8101))!=0,'8101端口已占用')
    return capacity()


def write_report(output, data):
    rows=data['逐区'];first=rows[0]
    log=output/LABEL/'gc'/first['日志']
    lines=log.read_text().splitlines()
    selected=lines[first['起始行']-1:first['结束行']]
    relevant=[s for s in selected if any(t in s for t in ('GC路径资源：','GC路径选中：',
        'GC路径复制：','GC路径映射提交：','Zone 重置诊断：','ZC 统计：'))]
    (output/'首轮GC证据.log').write_text('\n'.join(relevant)+'\n')
    text=f'''# 实验2：关闭RR后的真实GC路径

这是8 GiB FEMU上的路径教学实验，不是模型收益或性能对照。设备严格RR、主机同步RR及后台RR均关闭，GC启动水平仍为20%。选区使用当前共同候选和安全检查下的最少有效量Greedy，保留已有迁移预留、分配回退及内容隔离；不冒称未经修改的stock ZenFS。

本次观察到 {data['已观察GC轮数']} 轮GC、{data['已闭环源Zone数']} 次完成复制、映射提交及Reset的源区维护闭环，涉及 {data['不同源Zone数']} 个不同Zone，RR维护为0次。全无效区直接Reset不单独冒充一次带有效迁移的ZC。

第一个闭环源区为Zone {first['源Zone编号']}，字节起点 {first['源Zone字节地址']}，选择时有效量 {first['有效字节']} 字节，已用或封闭区域减有效量为 {first['回收余量字节']} 字节；该差值可能含Finish放弃尾部，不直接称为真实已写无效数据。实际复制有效量 {first['复制有效字节']} 字节，目标Zone为 {first['目标Zone编号']}。

源Zone Reset成功，设备后置报告为Empty、WP回到Zone起点，主机有效量为0。该轮前后设备资源分别为 {first['设备资源前']} 和 {first['设备资源后']}。这些是设备报告时的实测计数，包含元数据等全盘Zone；主机token是另外的预算，日志中另列。并发业务可在两次报告之间改变资源，不能把净变化全部归因于单个源区。

迁移统计来自ZenFS的MigrationStats，可能包含稀疏头，但不保证包含全部对齐填充，不等于真实NAND编程量。有效量以主机有效extent计；封闭范围减有效量还可能含稀疏头及填充，不能全部解释成用户数据失效。

顺序证据为：GC检查空闲比例达到启动条件 → 快照及安全候选 → 最少有效量选择 → 获取目标 → 复制 → 成功提交文件映射 → 释放旧引用 → ResetUnusedIOZones重置无有效引用的源区 → 后置设备报告及ZC统计。原始行在 `{log}` 第 {first['起始行']} 至 {first['结束行']} 行，便于按顺序阅读。

`首轮GC证据.log` 提取同一轮的主要事件；`路径核验.json` 保存逐区、逐轮明细。复制后的映射提交和源Reset都经过核验；独立readseq和verifychecksum用于检查键数量与数据块校验和，不冒称逐请求最新值审计。

设备状态报告和主机有效量按顺序读取，属于观察窗口，不是跨层原子快照。新日志开关只在本次子镜像开启，原模型、旧Baseline及已有结果均保留。普通qcow2只保存系统与软件，不保存关机后的模拟NAND或RC。
'''
    (output/'实验2分析.md').write_text(text)


def execute(args,password,output):
    run=GcRun(args,password,output,'gc',8101,PARENT);error=None
    try:
        run.start();run.ssh(f'test ! -e {run.target} && mkdir {run.target}');run.deployed=True
        run.ssh(f'find /tmp -maxdepth 1 -type f -name "zenfs_nvme0n1_*.log" -print > {run.target}/初始日志清单.txt')
        expected=json.loads((SOURCE/'同库身份.json').read_text())
        query='cd /home/femu/rocksdb && sha256sum '+' '.join(expected)
        require(digest_manifest(run.ssh(query,180))==expected,'父镜像中的库、模型或客户端身份不符')
        run.command(run.scp()+[f'femu@127.0.0.1:/home/femu/rocksdb/plugin/zenfs/fs/fs_zenfs.cc',
            str(output/'源码/fs_zenfs.before.cc')])
        old=(output/'源码/fs_zenfs.before.cc').read_text()
        (output/'源码/gc-path.patch').write_text(make_patch(old))
        run.command(run.scp()+[str(output/'源码'/n) for n in ('gc-path.patch','gc_path_trace.py','run_gc_path_guest.sh')]
            +[f'femu@127.0.0.1:{run.target}/'])
        run.record(阶段='只读路径补丁应用及编译')
        run.ssh(f'cd /home/femu/rocksdb/plugin/zenfs && git apply --check {run.target}/gc-path.patch && '
            f'git apply {run.target}/gc-path.patch && git apply --reverse --check {run.target}/gc-path.patch && '
            'cd /home/femu/rocksdb && make DEBUG_LEVEL=1 LIB_MODE=static USE_RTTI=0 ROCKSDB_PLUGINS=zenfs -j2 db_bench '
            f'> {run.target}/编译.log 2>&1',900)
        unchanged={k:v for k,v in expected.items() if k not in ('librocksdb_debug.a','db_bench','plugin/zenfs/fs/fs_zenfs.cc')}
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(unchanged),180))==unchanged,
            '观测补丁意外改变模型或其他源')
        stamp(output/'观测库身份.json',digest_manifest(run.ssh(query,180)))
        run.ssh('cd /home/femu/rocksdb && '+
            f'./db_bench --db={run.target}/posix --benchmarks=fillseq,readseq,verifychecksum '
            '--num=1024 --value_size=4096 --disable_wal=1 --compression_type=none '+
            f'> {run.target}/普通目录小测.log 2>&1',120)
        run.record(阶段='编译和普通目录小测通过，启动纯GC路径实验')
        run.root(f'nohup setsid bash {run.target}/run_gc_path_guest.sh '
            f'< /dev/null > {run.target}/启动.log 2>&1 & echo $! > {run.target}/launcher.pid')
        started=time.monotonic()
        while time.monotonic()-started<1800:
            disk_space()
            result=run.ssh(f'if test -f {run.target}/gc/finished; then cat {run.target}/gc/wrapper.exit; '
                f'else echo 运行中; fi; test ! -f {run.target}/gc/phase.txt || cat {run.target}/gc/phase.txt; '
                f'test ! -f {run.target}/gc/bench.pid || ps -p "$(cat {run.target}/gc/bench.pid)" -o pid=,etime=,rss=; true')
            run.record(阶段='纯GC路径采证',最近观测=result)
            print('当前GC实验：'+result.strip().replace('\n','；'),flush=True)
            if result.splitlines()[0]!='运行中':
                require(result.splitlines()[0]=='0','纯GC实验失败，保留原始日志')
                break
            time.sleep(10)
        else:raise RuntimeError('GC路径实验超过30分钟，采证并正常关机，不无限追加负载')
        run.record(阶段='业务及客机核验完成，归档并正常关机')
    except BaseException as failure:error=failure;run.record(原始错误=repr(failure));raise
    finally:
        # 本实验不使用旧采样器；失败时主动收集/tmp原始日志，再归档关机。
        if run.deployed:
            try:
                command="from pathlib import Path;import shutil;p=Path("+repr(run.target)+");"+\
                    "old=set((p/'初始日志清单.txt').read_text().splitlines());"+\
                    "(p/'现场日志').mkdir(exist_ok=True);"+\
                    "[shutil.copyfile(x,p/'现场日志'/x.name) for x in Path('/tmp').glob('zenfs_nvme0n1_*.log') if str(x) not in old]"
                import shlex
                run.root('python3 -c '+shlex.quote(command),30)
            except Exception as failure:run.record(现场采证反馈=repr(failure))
        try:run.finish(error)
        except Exception as cleanup:
            if error is None:raise
            run.record(收尾另有错误=repr(cleanup))
    data=analyze(output/LABEL/'gc')
    require(data['已闭环源Zone数']>0,'没有完整有效数据迁移闭环')
    decisions=0
    for path in (output/LABEL/'gc').glob('*.zenfs.log'):
        for plan in model_decisions(path.read_text().splitlines()):
            safe=[r for r in plan['成本'] if r['安全']=='1']
            require(all(int(r['RR抵扣'])==0 for r in safe),'纯GC实验出现RR成本抵扣')
            best=min(safe,key=lambda r:(int(r['有效']),int(r['Zone']))) if safe else None
            require(int(plan['决策']['实际Zone'])==(int(best['Zone']) if best else 0),
                '实际选择不是当前安全候选中最少有效量Greedy')
            decisions+=1
    require(decisions>=data['已观察GC轮数'],'GC轮次缺少可复核的Greedy决策')
    data['Greedy零RR抵扣复核轮数']=decisions
    verify=(output/LABEL/'gc/verify.log').read_text()
    require('1048576 operations;' in verify and 'verifychecksum' in verify,'独立全键读取/校验结果不完整')
    stamp(output/'路径核验.json',data);write_report(output,data)
    run.record(阶段='实验2完成，纯GC路径已核验，客机已退出',已观察GC轮数=data['已观察GC轮数'],
        已闭环源Zone数=data['已闭环源Zone数'],RR维护次数=0)
    print('实验2完成：'+str(output/'实验2分析.md'),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();args.label=LABEL
    budget=preflight();output=IMAGE_DIR/(LABEL+'-results')
    require(not output.exists(),'不覆盖已有教学实验')
    if args.check:print(json.dumps(budget,ensure_ascii=False,indent=2));return
    password=getpass.getpass('纯GC路径实验认证（仅内存）：')
    output.mkdir();(output/'源码').mkdir()
    for name in ('run_gc_path_host.py','run_gc_path_guest.sh','gc_path_trace.py'):
        shutil.copyfile(ROOT/'tests'/name,output/'源码'/name)
    stamp(output/'预注册计划.json',dict(目的='实验2：关闭RR，解释真实GC触发、选择、复制、映射提交、Reset和资源变化',
        平台='8GiB，64Zone，每Zone128MiB，Active上限6，单台串行，未锁页',父镜像=str(PARENT),
        设备严格RR=False,主机同步RR=False,后台RR=False,GC启动百分比=20,
        选区='与前轮共同候选及安全检查相同的最少有效量Greedy；无未来RR成本项',
        负载='原生db_bench，4GiB顺序装载，最多四轮各1GiB覆盖写，首次观察有效迁移后结束构造',
        变更='仅独立子镜像的fs_zenfs.cc增加默认关闭只读观测；不修改既有基线或模型',
        验证='源哈希、补丁可应用可回退、编译、小测、真实复制映射Reset闭环、readseq及verifychecksum',
        资源=budget,收尾='归档校验后正常关机；不保存模拟NAND完整快照'))
    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:execute(args,password,output)
    finally:password=None


if __name__=='__main__':main()
