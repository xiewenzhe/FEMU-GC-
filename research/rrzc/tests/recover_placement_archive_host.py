"""只启动已退出的本轮系统盘恢复落盘证据；不接FEMU，不恢复NAND，不重跑请求。"""
import argparse
import getpass
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
from run_rr_path_host import RrRun,ROOT,IMAGE_DIR
from run_calibration_host import detach_supervisor
from image_lifecycle import ImageLease,file_identity,write_json
from result_storage import compact
from ycsb_af import digest,require


def execute(password,original,output,state,entry):
    label=state['标签']+'-recover';image=Path(entry['image'])
    run=RrRun(SimpleNamespace(label=label),password,output,'archive',8101,Path(entry['parent']))
    run.qmp=Path('/tmp')/(label+'.qmp');run.socket='/tmp/'+label+'-ssh.sock'
    pidfile=Path('/tmp')/(label+'.pid');require(not any(p.exists() for p in (run.qmp,Path(run.socket),pidfile)),'控制路径已存在')
    archive=None;sha=None
    try:
        argv=[str(ROOT.parents[1]/'build-femu/qemu-system-x86_64'),'-name','FEMU-'+label,
            '-enable-kvm','-cpu','host','-smp','2','-m','2G','-device','virtio-scsi-pci,id=scsi0',
            '-device','scsi-hd,drive=hd0','-drive',f'file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0',
            '-net','user,hostfwd=tcp:127.0.0.1:8101-:22','-net','nic,model=virtio','-display','none',
            '-serial','none','-monitor','none','-qmp',f'unix:{run.qmp},server,nowait','-pidfile',str(pidfile),
            '-daemonize','-L',str(ROOT.parents[1]/'pc-bios')]
        subprocess.run(argv,cwd=ROOT.parents[1],check=True)
        run.record(当前客机PID=int(pidfile.read_text()),当前镜像=str(image),QMP=str(run.qmp),
            阶段='2GiB系统盘恢复证据；不接FEMU，不运行业务')
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
            require('Permission denied' not in reply and 'Host key verification failed' not in reply,'认证拒绝')
            time.sleep(4)
        else:raise RuntimeError('恢复SSH未就绪')
        target='/home/femu/'+state['标签'];run.ssh(f'test -f {target}/数据库组成.bin')
        code="from pathlib import Path;import os,hashlib,json;r=Path("+repr(target)+");o=r.stat();rows=[]\n"
        code+="for p in r.rglob('*.bin'):\n if p.is_file() and not p.is_symlink():\n  h=hashlib.sha256(p.read_bytes()).hexdigest();os.chown(p,o.st_uid,o.st_gid);assert hashlib.sha256(p.read_bytes()).hexdigest()==h;rows.append([str(p.relative_to(r)),h])\n"
        code+="(r/'采证权限恢复.json').write_text(json.dumps(rows,ensure_ascii=False))"
        run.root('python3 -c '+shlex.quote(code),60)
        remote=target+'-recovered.tar.gz'
        run.ssh(f'test ! -e {remote} && tar --exclude="{state["标签"]}/posix" --exclude="{state["标签"]}/传输.tar.gz" -czf {remote} -C /home/femu {state["标签"]}',180)
        sha=run.ssh('sha256sum '+remote).split()[0];archive=original/Path(remote).name
        require(not archive.exists() and not (original/state['标签']).exists(),'不覆盖旧证据')
        run.command(run.scp()+[f'femu@127.0.0.1:{remote}',str(original)],180)
        require(digest(archive)==sha,'恢复归档哈希不同')
        run.command(['tar','-xzf',str(archive),'-C',str(original)],120)
    finally:
        if pidfile.exists() and run.state.get('当前客机PID') is None:run.state.update(当前客机PID=int(pidfile.read_text()),当前镜像=str(image))
        run.close_guest();pid=run.state.get('当前客机PID');require(not pid or not Path('/proc',str(pid)).exists(),'恢复客机仍存活')
        run.record(当前客机PID=None,当前镜像=None,阶段='恢复采证结束，客机正常退出')
        subprocess.run(['ssh','-S',run.socket,'-O','exit','-p','8101','femu@127.0.0.1'],capture_output=True,timeout=15)
    require(archive is not None and sha,'恢复证据不完整')
    lease=ImageLease(IMAGE_DIR,image,Path(entry['parent']),original,ROOT.parents[1]/'build-femu/qemu-img')
    lease.data=entry;lease.finish(archive,sha,archived=True,guest_exited=True)
    state.update(当前客机PID=None,当前镜像=None,镜像处理=lease.data,阶段='失败证据完整恢复并退出，原失败保留',
        归档边界='只恢复系统盘上的日志；原NAND内存状态没有保存或恢复')
    write_json(original/'状态.json',state)
    write_json(original/'阶段归档.json',dict(完整=True,客机仍存活=False,业务成功=False,
        原始错误=state.get('原始错误'),原失败记录=str(output/'原阶段归档.json')))
    compact(original)
    payload=original/'传输.tar.gz'
    if payload.exists():payload.unlink()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('label');args=p.parse_args()
    require(args.label.startswith('joint-controlled-placement-') and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.label),'目标非法')
    original=IMAGE_DIR/(args.label+'-results');state=json.loads((original/'状态.json').read_text());entry=state['镜像处理']
    require(state['当前客机PID'] is None and entry['status']=='retained-incomplete' and
        file_identity(Path(entry['image']))==entry['identity'],'不是本轮已退出待恢复盘')
    listing=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
    require(str(IMAGE_DIR)+'/' not in listing,'已有本项目客机，不叠加恢复客机')
    require(not subprocess.run(['pgrep','-f','^.*qemu-system-x86_64 .*file='+entry['image']],capture_output=True).stdout,'目标仍在运行')
    with socket.socket() as s:require(s.connect_ex(('127.0.0.1',8101))!=0,'恢复端口已占用')
    memory=dict(s.split(':',1) for s in Path('/proc/meminfo').read_text().splitlines())
    require(int(memory['MemAvailable'].split()[0])*1024>=18<<30,'恢复内存保护余量不足')
    stat=os.statvfs(IMAGE_DIR);require(stat.f_bavail*stat.f_frsize>=28<<30,'恢复磁盘保护余量不足')
    output=original/'磁盘日志恢复';require(not output.exists(),'不覆盖恢复现场')
    password=getpass.getpass('只恢复落盘证据认证（仅内存）：');output.mkdir()
    for n in ('状态.json','阶段归档.json'):shutil.copyfile(original/n,output/('原'+n))
    shutil.copyfile(__file__,output/'恢复源码.py')
    if not detach_supervisor(output):return
    try:execute(password,original,output,state,entry)
    finally:password=None


if __name__=='__main__':main()
