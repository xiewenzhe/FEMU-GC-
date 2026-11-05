"""只启动已停止系统盘取回失败日志，不接FEMU设备或恢复NAND。"""
import getpass
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from types import SimpleNamespace
import pexpect
from run_calibration_host import IMAGE_DIR,PARENT,ROOT,detach_supervisor
from run_rr_path_host import RrRun
from image_lifecycle import ImageLease,file_identity,write_json
from ycsb_af import require,digest

LABEL='joint-controlled-pool-tail-stream-v3-20261104'
GUEST='joint-controlled-integrated-pool-v2-20261104'


def execute(password,original,output,state,entry):
    label=LABEL+'-archive-recovery';image=Path(entry['image'])
    run=RrRun(SimpleNamespace(label=label),password,output,'archive',8101,PARENT)
    run.qmp=Path('/tmp')/(label+'.qmp');run.socket='/tmp/'+label+'-ssh.sock'
    pidfile=Path('/tmp')/(label+'.pid');require(not any(p.exists() for p in (run.qmp,Path(run.socket),pidfile)),'恢复控制路径已存在')
    archive=None;sha=None;success=False
    try:
        args=[str(ROOT.parents[1]/'build-femu/qemu-system-x86_64'),'-name','FEMU-'+label,
            '-enable-kvm','-cpu','host','-smp','2','-m','2G','-device','virtio-scsi-pci,id=scsi0',
            '-device','scsi-hd,drive=hd0','-drive',f'file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0',
            '-net','user,hostfwd=tcp:127.0.0.1:8101-:22','-net','nic,model=virtio',
            '-display','none','-serial','none','-monitor','none','-qmp',f'unix:{run.qmp},server,nowait',
            '-pidfile',str(pidfile),'-daemonize','-L',str(ROOT.parents[1]/'pc-bios')]
        subprocess.run(args,cwd=ROOT.parents[1],check=True)
        run.record(当前客机PID=int(pidfile.read_text()),当前镜像=str(image),QMP=str(run.qmp),
            阶段='仅2GiB系统盘恢复失败日志',边界='未接FEMU模拟NAND，不运行业务，不恢复RC或物理状态')
        options=['-o','ControlMaster=yes','-o',f'ControlPath={run.socket}','-o','ControlPersist=600',
            '-o','PreferredAuthentications=password','-o','PubkeyAuthentication=no','-o','StrictHostKeyChecking=yes',
            '-o','HostKeyAlias=[127.0.0.1]:8089','-o','ConnectTimeout=8','-p','8101','femu@127.0.0.1','id']
        for _ in range(18):
            child=pexpect.spawn('ssh',options,encoding='utf-8',timeout=15)
            result=child.expect([r'[Pp]assword:',pexpect.EOF,pexpect.TIMEOUT])
            if result==0:
                child.sendline(password);child.expect(pexpect.EOF);reply=child.before;child.close()
                require(child.exitstatus==0 and 'uid=1000(femu)' in reply,'恢复认证失败');break
            reply=child.before;child.close(force=True)
            require('Permission denied' not in reply and 'Host key verification failed' not in reply,'恢复认证拒绝')
            time.sleep(4)
        else:raise RuntimeError('恢复SSH未就绪')
        target='/home/femu/'+GUEST
        run.ssh(f'test -f {target}/p1/请求提交.bin && test -f {target}/p1/run.log && test -f {target}/持续负载构建.json')
        remote=f'/home/femu/{GUEST}-calibration-recovered.tar.gz'
        run.ssh(f'test ! -e {remote} && tar --exclude="{GUEST}/posix" --exclude="{GUEST}/固定输入复用.tar" '
            f'--exclude="{GUEST}/逻辑输入/*.bin.gz" -czf {remote} -C /home/femu {GUEST}',240)
        sha=run.ssh('sha256sum '+remote).split()[0];archive=original/Path(remote).name
        require(not archive.exists(),'恢复归档已存在，不覆盖')
        run.command(run.scp()+[f'femu@127.0.0.1:{remote}',str(original)],240)
        require(digest(archive)==sha,'恢复归档宿主哈希不同')
        require(not (original/GUEST).exists(),'已存在提取目录，不覆盖')
        run.command(['tar','-xzf',str(archive),'-C',str(original)],180);success=True
    finally:
        if pidfile.exists() and run.state.get('当前客机PID') is None:
            run.state.update(当前客机PID=int(pidfile.read_text()),当前镜像=str(image))
        run.close_guest();pid=run.state.get('当前客机PID')
        require(not pid or not Path('/proc',str(pid)).exists(),'恢复客机仍存活')
        run.record(当前客机PID=None,当前镜像=None,阶段='失败磁盘日志恢复结束并正常退出',恢复完成=success)
        subprocess.run(['ssh','-S',run.socket,'-O','exit','-p','8101','femu@127.0.0.1'],capture_output=True,timeout=15)
        if success:
            lease=ImageLease(IMAGE_DIR,image,Path(entry['parent']),original,ROOT.parents[1]/'build-femu/qemu-img')
            lease.data=entry
            lease.data['archive_input_reference']=state['结果保存策略']['固定输入引用']
            lease.finish(archive,sha,archived=True,guest_exited=True)
            state.update(阶段='失败业务已中止，磁盘证据恢复完整并退出',当前客机PID=None,当前镜像=None,
                镜像处理=lease.data,归档恢复目录=str(output),归档边界='仅恢复系统盘文件，原业务失败，未恢复NAND')
            write_json(original/'状态.json',state)
            write_json(original/'阶段归档.json',dict(完整=True,客机仍存活=False,业务成功=False,
                原始错误=state.get('原始错误'),原归档失败证据=str(output/'原阶段归档.json'),恢复归档SHA256=sha))


def main():
    original=IMAGE_DIR/(LABEL+'-results');state=json.loads((original/'状态.json').read_text())
    entry=state['镜像处理'];image=Path(entry['image'])
    require(state['当前客机PID'] is None and entry['status']=='retained-incomplete'
        and file_identity(image)==entry['identity'],'不是本轮已退出归档失败临时盘')
    listing=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
    require(str(IMAGE_DIR)+'/' not in listing,'本项目已有客机，不叠加')
    with socket.socket() as sock:require(sock.connect_ex(('127.0.0.1',8101))!=0,'端口已占用')
    output=original/'磁盘日志恢复';require(not output.exists(),'不覆盖恢复记录')
    memory=dict(s.split(':',1) for s in Path('/proc/meminfo').read_text().splitlines())
    require(int(memory['MemAvailable'].split()[0])*1024>=18<<30,'恢复2GiB及16GiB保护余量不足')
    space=os.statvfs(IMAGE_DIR);require(space.f_bavail*space.f_frsize>=28<<30,'恢复归档及原磁盘保护余量不足')
    password=getpass.getpass('失败日志恢复认证（仅内存）：');output.mkdir()
    for n in ('状态.json','阶段归档.json'):shutil.copyfile(original/n,output/('原'+n))
    shutil.copyfile(__file__,output/'恢复源码.py')
    if not detach_supervisor(output):return
    try:execute(password,original,output,state,entry)
    finally:password=None


if __name__=='__main__':main()
