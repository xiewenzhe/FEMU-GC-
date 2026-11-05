#!/usr/bin/env python3
"""复用已退出的失败临时系统盘取证；不创建镜像、不接模拟SSD、不恢复NAND状态。"""
import getpass
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from types import SimpleNamespace
import pexpect

from run_rr_path_host import RrRun, IMAGE_DIR, ROOT
from run_all_blocks_followup_host import PARENT
from ycsb_af import require, digest

LABEL='joint-repeat-evidence-v1-20261026'
OLD=IMAGE_DIR/'joint-repeat-order-v1-20261026-results'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only',action='store_true')
    parser.add_argument('--attempt',type=int,default=1)
    options=parser.parse_args()
    require(1<=options.attempt<=3,'取证尝试次数超限')
    suffix='' if options.attempt==1 else '-a'+str(options.attempt)
    label=('joint-repeat-evidence-v2-20261026' if options.source_only else LABEL)+suffix
    image=IMAGE_DIR/'joint-repeat-order-v1-20261026-repeat.qcow2'
    lease=json.loads((OLD/'镜像生命周期.json').read_text())
    require(lease['kind']=='temporary' and lease['status']=='retained-unverified','失败镜像身份或角色不符')
    stat=image.stat();require([stat.st_dev,stat.st_ino]==lease['identity'],'失败镜像inode变化')
    stage=json.loads((OLD/'阶段归档.json').read_text())
    require(stage['完整'] and not stage['客机仍存活'],'原实验未归档退出')
    listing=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
    require(str(IMAGE_DIR)+'/' not in listing,'还有本项目客机，不叠加')
    with socket.socket() as s:require(s.connect_ex(('127.0.0.1',8101))!=0,'8101已占用')
    mem=dict(s.split(':',1) for s in Path('/proc/meminfo').read_text().splitlines())
    require(int(mem['MemAvailable'].split()[0])*1024>=18<<30,'2GiB取证及16GiB余量不足')
    output=OLD/(('源码恢复' if options.source_only else '证据恢复')+suffix);require(not output.exists(),'不覆盖恢复证据')
    password=getpass.getpass('失败现场取证认证（仅内存）：')
    output.mkdir()
    run=RrRun(SimpleNamespace(label=label),password,output,'evidence',8101,PARENT)
    run.qmp=Path('/tmp')/(label+'.qmp');run.socket='/tmp/'+label+'-ssh.sock'
    pidfile=Path('/tmp')/(label+'.pid')
    require(not any(p.exists() for p in [run.qmp,Path(run.socket),pidfile]),'取证控制文件已存在')
    argv=[str(ROOT.parents[1]/'build-femu/qemu-system-x86_64'),'-name','FEMU-'+label,
        '-enable-kvm','-cpu','host','-smp','2','-m','2G','-device','virtio-scsi-pci,id=scsi0',
        '-device','scsi-hd,drive=hd0','-drive',f'file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0',
        '-net','user,hostfwd=tcp:127.0.0.1:8101-:22','-net','nic,model=virtio',
        '-display','none','-serial','none','-monitor','none','-qmp',f'unix:{run.qmp},server,nowait',
        '-pidfile',str(pidfile),'-daemonize']
    error=None
    try:
        r=subprocess.run(argv,capture_output=True,text=True,timeout=90)
        (output/'启动.log').write_text(r.stdout+r.stderr)
        require(r.returncode==0,'取证客机启动失败')
        run.record(阶段='失败系统盘取证，未接模拟SSD',当前客机PID=int(pidfile.read_text()),当前镜像=str(image),
                   内存预算字节=2<<30,所属用途='取回已落盘日志和源码，不运行业务、不保存或恢复NAND')
        args=['-o','ControlMaster=yes','-o',f'ControlPath={run.socket}','-o','ControlPersist=600',
            '-o','PreferredAuthentications=password','-o','PubkeyAuthentication=no',
            '-o','StrictHostKeyChecking=yes','-o','HostKeyAlias=[127.0.0.1]:8089',
            '-o','ConnectTimeout=8','-p','8101','femu@127.0.0.1','id']
        for _ in range(18):
            child=pexpect.spawn('ssh',args,encoding='utf-8',timeout=15)
            response=child.expect([r'[Pp]assword:',pexpect.EOF,pexpect.TIMEOUT])
            if response==0:
                child.sendline(password);child.expect(pexpect.EOF);text=child.before;child.close()
                require(child.exitstatus==0 and 'uid=1000(femu)' in text,'取证认证失败')
                break
            text=child.before;child.close(force=True)
            require('Permission denied' not in text and 'Host key verification failed' not in text,'认证或密钥不符')
            time.sleep(4)
        else:raise RuntimeError('取证SSH未就绪')
        capture='' if options.source_only else f'cp /tmp/zenfs_nvme0n1_2026-10-26_02:33:49.log {run.target}/verify.zenfs.log && '
        run.ssh(f'test ! -e {run.target} && mkdir {run.target} && mkdir {run.target}/fs && '
            f'cp /home/femu/rocksdb/plugin/zenfs/fs/*.cc /home/femu/rocksdb/plugin/zenfs/fs/*.h {run.target}/fs/ && '
            +capture+f'sha256sum {run.target}/fs/* > {run.target}/files.sha256',60)
        remote=run.target+'.tar.gz'
        run.ssh(f'tar -czf {remote} -C /home/femu {label}',60)
        sha=run.ssh('sha256sum '+remote).split()[0]
        run.command(run.scp()+[f'femu@127.0.0.1:{remote}',str(output)],60)
        archive=output/Path(remote).name;require(digest(archive)==sha,'恢复归档哈希不符')
        subprocess.run(['tar','-xzf',str(archive),'-C',str(output)],check=True)
        run.record(阶段='实际源码已恢复' if options.source_only else '失败校验完整日志和实际源码已恢复',归档SHA256=sha,
                   证据限制='原/tmp失败日志未被原入口归档且重启后已清除，不能声称恢复完整失败日志' if options.source_only else '')
    except BaseException as failure:
        error=failure;run.record(原始错误=repr(failure));raise
    finally:
        if pidfile.exists() and run.state.get('当前客机PID') is None:
            run.state.update(当前客机PID=int(pidfile.read_text()),当前镜像=str(image))
        run.close_guest()
        pid=run.state.get('当前客机PID')
        alive=bool(pid and Path('/proc',str(pid)).exists())
        require(not alive,'取证客机未退出')
        run.record(当前客机PID=None,当前镜像=None,阶段='取证完成并正常关机' if error is None else '取证失败，客机已退出')
        subprocess.run(['ssh','-S',run.socket,'-O','exit','-p','8101','femu@127.0.0.1'],capture_output=True,timeout=15)
        password=None


if __name__=='__main__':main()
