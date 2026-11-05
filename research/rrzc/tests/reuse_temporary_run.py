"""继续使用已归档退出的本任务临时系统盘；不创建镜像或将临时盘用作父镜像。"""
import json
import os
from pathlib import Path
import time
import shutil
import pexpect
from image_lifecycle import ImageLease,file_identity,info
from run_rr_path_host import RrRun,IMAGE_DIR,ROOT
from run_joint_host import OLD_DEVICE
from ycsb_af import require


class ReuseRun(RrRun):
    max_image_bytes=32 << 30
    next_run_image_budget=8 << 30

    def start(self):
        image=Path(self.args_reuse_image)
        require(image.parent==IMAGE_DIR and image.is_file() and not image.is_symlink(),'复用镜像路径非法')
        entry=IMAGE_DIR/'.image-lifecycle'/(image.name+'.json')
        require(entry.is_file(),'不能复用未登记的历史镜像')
        old=json.loads(entry.read_text())
        require(old['kind']=='temporary' and old['status']=='retained-unverified','镜像不是已结束待核验临时盘')
        previous=json.loads((Path(old['output'])/'阶段归档.json').read_text())
        require(previous['完整'] and not previous['客机仍存活'],'前次运行未归档退出')
        require(file_identity(image)==old['identity'],'镜像身份变化')
        allocated=image.stat().st_blocks*512
        require(allocated+self.next_run_image_budget<=self.max_image_bytes,
                '复用镜像空间预算不足：已占'+str(allocated)+'字节；请先整理工作盘，不继续增长')
        binary=ROOT.parents[1]/'build-femu/qemu-img'
        metadata=info(binary,image)
        require(Path(metadata['full-backing-filename']).resolve()==self.parent_image.resolve(),'原构建父镜像不同')
        self.image_lease=ImageLease(IMAGE_DIR,image,self.parent_image,self.output,binary)
        self.image_lease.data=old
        shutil.copyfile(Path(__file__).with_name('image_lifecycle.py'),self.output/'镜像管理源码.py')
        history=old.setdefault('history_outputs',[])
        if old['output'] not in history:history.append(old['output'])
        old.update(output=str(self.output),status='reusing',supervisor_pid=os.getpid(),
                   reuse_reason='同一临时工作盘修复编译，不创建版本镜像或子链')
        self.image_lease.save()
        self.qmp=Path('/tmp')/(self.label_prefix+'-'+self.tag+'.qmp')
        self.socket='/tmp/'+self.label_prefix+'-'+self.tag+'-ssh.sock'
        pid=Path('/tmp')/(self.label_prefix+'-'+self.tag+'.pid')
        require(not any(p.exists() for p in (self.qmp,Path(self.socket),pid)),'复用控制文件已存在')
        self.command(['./build-femu/qemu-system-x86_64','-name','FEMU-'+self.label_prefix,
            '-enable-kvm','-cpu','host','-smp','4','-m','4G','-device','virtio-scsi-pci,id=scsi0',
            '-device','scsi-hd,drive=hd0','-drive',f'file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0',
            '-device',OLD_DEVICE,'-net',f'user,hostfwd=tcp:127.0.0.1:{self.port}-:22','-net','nic,model=virtio',
            '-display','none','-serial','none','-monitor','none','-qmp',f'unix:{self.qmp},server,nowait',
            '-pidfile',str(pid),'-daemonize'])
        self.record(阶段='同一临时镜像复用启动，未新建镜像',当前客机PID=int(pid.read_text()),当前镜像=str(image))
        args=['-o','ControlMaster=yes','-o',f'ControlPath={self.socket}','-o','ControlPersist=3600',
              '-o','PreferredAuthentications=password','-o','PubkeyAuthentication=no','-o','StrictHostKeyChecking=yes',
              '-o','HostKeyAlias=[127.0.0.1]:8089','-o','ConnectTimeout=8','-p',self.port,'femu@127.0.0.1','id']
        for _ in range(18):
            child=pexpect.spawn('ssh',args,encoding='utf-8',timeout=15)
            response=child.expect([r'[Pp]assword:',pexpect.EOF,pexpect.TIMEOUT])
            if response==0:
                child.sendline(self.password);child.expect(pexpect.EOF);text=child.before;child.close()
                require(child.exitstatus==0 and 'uid=1000(femu)' in text,'认证失败')
                return
            text=child.before;child.close(force=True)
            require('Permission denied' not in text and 'Host key verification failed' not in text,'认证或密钥不符')
            time.sleep(4)
        raise RuntimeError('复用客机SSH未就绪')
