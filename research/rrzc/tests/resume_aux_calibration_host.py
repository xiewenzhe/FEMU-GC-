#!/usr/bin/env python3
"""仅接管共同输入已完成、尚未开始业务的辅助盘诊断；不重启客机或重造输入。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import signal
import shutil
import sys
import time
from types import SimpleNamespace

from run_calibration_host import CalibrationRun,IMAGE,IMAGE_DIR,ROOT,PARENT
from run_triggered_gc_host import wait_stage,stop_business,preserve_unverified_image
from image_lifecycle import ImageLease,file_identity
from run_joint_host import digest_manifest
from analyze_calibration import analyze
from analyze_flush_wait import analyze as analyze_wait
from ycsb_af import require,digest


def check_host(output,state,plan):
    require(output.parent==IMAGE_DIR and output.name==state['标签']+'-results','结果目录不符')
    require(plan['辅助目录tmpfs诊断'] and plan['固定逻辑输入'] and plan['Flush同步分段'], '不是指定辅助盘诊断')
    require(state['当前策略']=='source' and state['已完成']==[], '仅支持共同输入阶段恢复')
    require(not Path('/proc',str(state['监督进程PID'])).exists(),'原监督仍存活，不重复接管')
    pid=state['当前客机PID'];proc=Path('/proc',str(pid))
    require(proc.stat().st_uid==os.getuid(),'客机不属于本账号')
    args=(proc/'cmdline').read_bytes().split(b'\0')
    require(b'FEMU-'+state['标签'].encode() in args and any(b'file='+str(IMAGE).encode()+b',' in s for s in args), '客机身份不符')
    require(state['当前镜像']==str(IMAGE) and state['SSH端口']==8101,'镜像或端口不符')
    entry=json.loads((IMAGE_DIR/'.image-lifecycle'/(IMAGE.name+'.json')).read_text())
    require(entry['kind']=='temporary' and entry['status']=='reusing' and entry['output']==str(output)
        and file_identity(IMAGE)==entry['identity'],'镜像生命周期身份不符')
    return entry


def continuation(output,state,entry,password):
    label=state['标签'];args=SimpleNamespace(label=label)
    run=CalibrationRun(args,password,output,'calibration',8101,PARENT,retain_image=False)
    run.state=state;run.deployed=True;run.device_event_trace=False
    run.qmp=Path('/tmp')/(label+'-calibration.qmp')
    run.socket='/tmp/'+label+'-calibration-ssh.sock'
    require(run.qmp.is_socket() and Path(run.socket).is_socket(),'既有控制连接缺失')
    run.image_lease=ImageLease(IMAGE_DIR,IMAGE,PARENT,output,ROOT.parents[1]/'build-femu/qemu-img')
    run.image_lease.data=entry
    run.image_lease.data['supervisor_pid']=os.getpid();run.image_lease.save()
    run.record(监督进程PID=os.getpid(),原监督进程PID=state['监督进程PID'],
        阶段='接管会话中断后的同一客机：核验共同输入，不重启不重放业务',
        监督恢复说明='原终端监督退出；独立共同输入任务正常完成，业务尚未开始，恢复时点明确记录')
    error=None;aux_started=False
    try:
        require(run.ssh(f'cat {run.target}/source/finished {run.target}/source/wrapper.exit').strip()=='0',
            '共同输入未正常完成')
        require(run.ssh(f'test ! -e {run.target}/g1 && test ! -e {run.target}/g2 && '
            f'test ! -e {run.target}/p1 && test ! -e {run.target}/p2 && test ! -e {run.target}/诊断辅助盘 && echo 就绪').strip()=='就绪',
            '已经存在后续阶段，拒绝重复执行')
        identity=json.loads((output/'校准客户端身份.json').read_text())
        require(run.ssh(f'sha256sum {run.target}/db_bench').split()[0]==identity['客户端SHA256'],'客户端身份变化')
        expected=json.loads((output/'源码/expected.json').read_text())
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(expected),180))==expected,
            '原库或源码身份变化')
        aux_started=True
        run.root(f'python3 {run.target}/aux_tmpfs_guest.py setup {run.target}',60)
        for tag in ('p1','p2'):
            run.record(阶段=tag+'仅核验起始逐块状态',当前策略=tag)
            wait_stage(run,tag,'run_prepare_only_guest.sh',600,mode='greedy')
        for tag in ('g1','g2'):
            if tag=='g1':
                run.root(f'nohup python3 {run.target}/flush_wait_observer.py {run.target} --seconds 1200 '
                    f'< /dev/null > {run.target}/Flush观察启动.log 2>&1 & echo $! > {run.target}/Flush观察.pid',30)
                run.record(客机只读观察PID=int(run.ssh(f'cat {run.target}/Flush观察.pid').strip()),
                    观察用途='最多1200秒或g2结束自动退出；异常由正常关机回收')
            run.record(阶段=tag+'辅助目录隔离下的固定请求Greedy',当前策略=tag)
            wait_stage(run,tag,'run_calibrated_guest.sh',2400,mode='greedy')
            run.record(已完成=[*run.state['已完成'],tag])
        require(digest_manifest(run.ssh('cd /home/femu/rocksdb && sha256sum '+' '.join(expected),180))==expected,
            '实验改变原库或模型')
    except BaseException as failed:
        error=failed;run.record(原始错误=repr(failed))
        try:stop_business(run)
        except Exception as stopped:run.record(业务中断反馈=repr(stopped))
    finally:
        if aux_started:
            try:
                run.root(f'python3 {run.target}/aux_tmpfs_guest.py finish {run.target}',120)
                run.record(辅助诊断盘='内容归档哈希核验通过，已正常卸载')
            except Exception as failed:
                run.record(辅助盘收尾错误=repr(failed))
                if error is None:error=failed
        try:run.finish(error)
        except Exception as cleanup:
            if not preserve_unverified_image(run,cleanup):raise
        run.record(客机只读观察PID=None,只读观察收尾='客机已正常退出，观察日志随归档保留')
    if error is not None:raise error
    data=analyze(output/label,output/'分析')
    waits=analyze_wait(output/label,output/'分析/Flush等待分段.json')
    quality=json.loads((output/'分析/重复性预检.json').read_text())
    run.record(阶段='辅助目录诊断已完成并正常关机',计时=data,Flush同步分段汇总=waits,
        重复性工程预检通过=quality['通过'],收益准入说明='tmpfs仅用于诊断，不能用于持久存储性能或模型收益结论')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('label')
    args=parser.parse_args();require(args.label=='joint-controlled-aux-tmpfs-v1-20261027','只接管本次已核验中断任务')
    output=IMAGE_DIR/(args.label+'-results');state=json.loads((output/'状态.json').read_text())
    plan=json.loads((output/'预注册计划.json').read_text());entry=check_host(output,state,plan)
    password=getpass.getpass('接管原客机认证（仅内存）：')
    shutil.copyfile(Path(__file__),output/'源码/resume_aux_calibration_host.py')
    (output/'接管前状态.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    # 认证留在内存后独立会话运行；不继承聊天终端，密码不写文件、环境或日志。
    pid=os.fork()
    if pid:
        print('独立接管监督PID='+str(pid),flush=True);return
    os.setsid()
    descriptor=os.open(output/'接管监督.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    os.dup2(descriptor,1);os.dup2(descriptor,2);os.close(descriptor)
    null=os.open('/dev/null',os.O_RDONLY);os.dup2(null,0);os.close(null)
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:continuation(output,state,entry,password)
    except BaseException:
        import traceback
        traceback.print_exc();sys.stdout.flush();sys.stderr.flush();os._exit(1)
    finally:password=None
    sys.stdout.flush();sys.stderr.flush();os._exit(0)


if __name__=='__main__':main()
