#!/usr/bin/env python3
"""只启动已退出临时系统盘取回落盘结果；不接FEMU模拟盘、不运行业务。"""
import getpass
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import time
from types import SimpleNamespace
import pexpect

from run_calibration_host import IMAGE,IMAGE_DIR,PARENT,ROOT,detach_supervisor
from run_rr_path_host import RrRun
from image_lifecycle import file_identity,write_json
from ycsb_af import require,digest

LABEL='joint-controlled-aux-tmpfs-v1-20261027'

VERIFY=r'''
import hashlib,json,os
from pathlib import Path
r=Path('/home/femu/joint-controlled-aux-tmpfs-v1-20261027')
p=r/'辅助盘归档';proof=json.loads((r/'辅助盘收尾.json').read_text())
assert proof['归档完整'] and proof['已卸载'] and p.is_dir() and not p.is_symlink()
assert not os.path.ismount(r/'诊断辅助盘')
for tag in ('g1','g2'):
    assert (r/tag/'finished').exists() and (r/tag/'wrapper.exit').read_text().strip()=='0'
    assert (r/tag/'run.zenfs.log').is_file() and (r/tag/'请求提交.bin').is_file()
actual={str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file()}
assert actual==proof['文件SHA256'], '落盘辅助内容与卸载前哈希不符'
owner=r.stat()
for f in [p,*p.rglob('*')]:
    assert not f.is_symlink(), '归档内不允许符号链接'
    os.chown(f,owner.st_uid,owner.st_gid)
(r/'辅助归档权限修复.json').write_text(json.dumps(dict(内容哈希复核通过=True,文件数=len(actual),
    仅修复本轮辅助归档目录账号=True,原业务不重跑=True,未连接模拟NAND=True),ensure_ascii=False,indent=2)+'\n')
print('落盘辅助内容哈希复核通过；两臂正常结束；归档账号已修复')
'''


def execute(password,original,output,state,entry):
    label=LABEL+'-archive-recovery'
    run=RrRun(SimpleNamespace(label=label),password,output,'archive',8101,PARENT)
    run.qmp=Path('/tmp')/(label+'.qmp');run.socket='/tmp/'+label+'-ssh.sock'
    pidfile=Path('/tmp')/(label+'.pid')
    require(not any(p.exists() for p in (run.qmp,Path(run.socket),pidfile)),'恢复控制文件已存在')
    remote='/home/femu/'+LABEL+'-calibration-recovered.tar.gz'
    error=None;archive=None;sha=None
    try:
        argv=[str(ROOT.parents[1]/'build-femu/qemu-system-x86_64'),'-name','FEMU-'+label,
            '-enable-kvm','-cpu','host','-smp','2','-m','2G','-device','virtio-scsi-pci,id=scsi0',
            '-device','scsi-hd,drive=hd0','-drive',f'file={IMAGE},if=none,aio=native,cache=none,format=qcow2,id=hd0',
            '-net','user,hostfwd=tcp:127.0.0.1:8101-:22','-net','nic,model=virtio',
            '-display','none','-serial','none','-monitor','none','-qmp',f'unix:{run.qmp},server,nowait',
            '-pidfile',str(pidfile),'-daemonize']
        # 取证明确不接FEMU设备，不走业务启动器强制添加/校验设备参数的路径。
        reply=subprocess.run(argv,cwd=ROOT.parents[1],capture_output=True,text=True,timeout=90)
        (output/'启动.log').write_text(reply.stdout+reply.stderr)
        require(reply.returncode==0,'系统盘取证启动失败')
        run.record(当前客机PID=int(pidfile.read_text()),当前镜像=str(IMAGE),阶段='只启动2GiB系统盘取回已落盘归档',
            边界='未接模拟NAND，不运行业务，不恢复物理块运行状态',QMP=str(run.qmp),所属实验='归档恢复')
        options=['-o','ControlMaster=yes','-o',f'ControlPath={run.socket}','-o','ControlPersist=600',
            '-o','PreferredAuthentications=password','-o','PubkeyAuthentication=no','-o','StrictHostKeyChecking=yes',
            '-o','HostKeyAlias=[127.0.0.1]:8089','-o','ConnectTimeout=8','-p','8101','femu@127.0.0.1','id']
        for _ in range(18):
            child=pexpect.spawn('ssh',options,encoding='utf-8',timeout=15)
            response=child.expect([r'[Pp]assword:',pexpect.EOF,pexpect.TIMEOUT])
            if response==0:
                child.sendline(password);child.expect(pexpect.EOF);text=child.before;child.close()
                require(child.exitstatus==0 and 'uid=1000(femu)' in text,'恢复认证失败');break
            text=child.before;child.close(force=True)
            require('Permission denied' not in text and 'Host key verification failed' not in text,'恢复认证或密钥失败')
            time.sleep(4)
        else:raise RuntimeError('恢复客机SSH未就绪')
        proof=run.root('python3 -c '+shlex.quote(VERIFY),90)
        (output/'落盘内容核验.log').write_text(proof)
        run.ssh(f'test ! -e {remote} && tar --exclude="{LABEL}/posix" -czf {remote} -C /home/femu {LABEL}',240)
        sha=run.ssh('sha256sum '+remote).split()[0]
        archive=original/Path(remote).name;require(not archive.exists(),'不覆盖宿主恢复归档')
        run.command(run.scp()+[f'femu@127.0.0.1:{remote}',str(original)],240)
        require(digest(archive)==sha,'恢复压缩包宿主哈希不符')
        require(not (original/LABEL).exists(),'不覆盖已提取结果')
        run.command(['tar','-xzf',str(archive),'-C',str(original)],180)
        run.record(阶段='两臂原始结果完整取回；正常关机',恢复归档=str(archive),SHA256=sha)
    except BaseException as failure:
        error=failure;run.record(原始错误=repr(failure));raise
    finally:
        if pidfile.exists() and run.state.get('当前客机PID') is None:
            run.state.update(当前客机PID=int(pidfile.read_text()),当前镜像=str(IMAGE))
        run.close_guest()
        pid=run.state.get('当前客机PID');require(not pid or not Path('/proc',str(pid)).exists(),'恢复客机尚未退出')
        run.record(当前客机PID=None,当前镜像=None,阶段='归档恢复结束，客机已正常退出')
        subprocess.run(['ssh','-S',run.socket,'-O','exit','-p','8101','femu@127.0.0.1'],capture_output=True,timeout=15)
        if error is None:
            entry.update(status='retained-unverified',archive=str(archive),archive_sha256=sha,
                reason='归档恢复完整；保留临时盘，尚未完成全部占用和依赖回收核验',supervisor_pid=os.getpid())
            write_json(IMAGE_DIR/'.image-lifecycle'/(IMAGE.name+'.json'),entry)
            write_json(original/'镜像生命周期.json',entry)
            state.update(阶段='两臂完成且归档权限修复后完整恢复；恢复客机已退出',当前客机PID=None,当前镜像=None,
                客机只读观察PID=None,镜像处理=entry,归档恢复目录=str(output),监督进程PID=None,
                归档边界='首次归档失败证据保留；仅恢复落盘结果，不恢复模拟NAND状态')
            write_json(original/'状态.json',state)
            write_json(original/'阶段归档.json',dict(完整=True,客机仍存活=False,业务成功=True,
                原始归档失败已保留=True,恢复归档=str(archive),SHA256=sha,原始错误=None))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt',type=int,choices=(1,2),default=1)
    args=parser.parse_args()
    original=IMAGE_DIR/(LABEL+'-results');state=json.loads((original/'状态.json').read_text())
    stage=json.loads((original/'阶段归档.json').read_text())
    require(state['当前客机PID'] is None and state['已完成']==['g1','g2'] and not stage['完整'], '不是本次归档失败现场')
    entry=json.loads((IMAGE_DIR/'.image-lifecycle'/(IMAGE.name+'.json')).read_text())
    require(entry['kind']=='temporary' and entry['status']=='retained-incomplete' and
        entry['output']==str(original) and file_identity(IMAGE)==entry['identity'], '临时镜像身份不符')
    listing=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
    require(str(IMAGE_DIR)+'/' not in listing,'本项目仍有客机，不叠加')
    with socket.socket() as s:require(s.connect_ex(('127.0.0.1',8101))!=0,'恢复端口已占用')
    mem=dict(s.split(':',1) for s in Path('/proc/meminfo').read_text().splitlines())
    require(int(mem['MemAvailable'].split()[0])*1024>=18<<30,'取证2GiB及16GiB保护余量不足')
    stat=os.statvfs(IMAGE_DIR);require(stat.f_bavail*stat.f_frsize>=28<<30,'归档及24GiB磁盘保护余量不足')
    output=original/('归档恢复' if args.attempt==1 else '归档恢复-v2');require(not output.exists(),'不覆盖恢复现场')
    password=getpass.getpass('归档恢复认证（仅内存）：')
    output.mkdir();shutil.copyfile(__file__,output/'恢复源码.py')
    shutil.copyfile(original/'阶段归档.json',output/'原归档失败.json')
    shutil.copyfile(original/'状态.json',output/'原状态.json')
    if not detach_supervisor(output):return
    try:execute(password,original,output,state,entry)
    finally:password=None


if __name__=='__main__':main()
