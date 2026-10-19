#!/usr/bin/env python3
"""串行监督固定900秒配对；只操作新子镜像，失败停留现场，不自动重跑。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

from run_stream_pair import Pair, require, PROJECT, IMAGE_DIR
from run_guarded_stream_host import main as run_arm

sys.path.insert(0,str(PROJECT/"research/rrzc"))
from analyze_guarded_stream import analyze, compare

LABEL="guarded-medium-20261015"
PARENT=IMAGE_DIR/"guarded-build-20261014.qcow2"
DEVICE="femu,devsz_mb=8192,namespaces=1,zns_num_ch=8,zns_num_lun=4,zns_num_plane=2,zns_num_blk=64,zns_flash_type=4,femu_mode=3,zns_rr_threshold=4096,zns_rr_history=on,zns_max_active=6"


def preflight():
    require(PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR,"父镜像不在获准位置")
    require(Path("/dev/kvm").exists(),"当前环境无法访问KVM")
    with socket.socket() as probe:
        require(probe.connect_ex(("127.0.0.1",8089))!=0,"8089已占用，停止而非操作已有客机")
    processes=subprocess.check_output(["ps","-C","qemu-system-x86_64","-o","args="],text=True)
    require(f"file={PARENT}," not in processes,"父镜像仍在运行")
    for strategy in ("greedy","guarded"):
        label=f"{LABEL}-{strategy}"
        for path in (IMAGE_DIR/f"{label}.qcow2",Path(f"/tmp/{label}.pid"),Path(f"/tmp/{label}.qmp"),
                     Path(f"/tmp/{label}-ssh.sock"),PROJECT/"research/rrzc/results"/label):
            require(not path.exists(),"本轮目标已存在，拒绝覆盖："+str(path))
    require(not (PROJECT/"research/rrzc/results"/LABEL).exists(),"配对结果目录已存在")
    require(os.statvfs(IMAGE_DIR).f_bavail*os.statvfs(IMAGE_DIR).f_frsize>=12*(1<<30),"data盘可用空间不足12GiB")


class GuardedPair(Pair):
    def start_vm(self,mode,parent):
        image=IMAGE_DIR/f"{LABEL}-{mode}.qcow2"
        pid=Path(f"/tmp/{LABEL}-{mode}.pid");qmp=Path(f"/tmp/{LABEL}-{mode}.qmp")
        with socket.socket() as probe:
            require(probe.connect_ex(("127.0.0.1",8089))!=0,"8089在启动前被占用")
        require(not any(p.exists() for p in (image,pid,qmp)),"镜像或控制文件已存在")
        processes=subprocess.check_output(["ps","-C","qemu-system-x86_64","-o","args="],text=True)
        require(f"file={parent}," not in processes,"父镜像仍在运行")
        self.command(["./build-femu/qemu-img","create","-f","qcow2","-F","qcow2","-b",str(parent),str(image)])
        self.command(["./build-femu/qemu-system-x86_64","-name",f"FEMU-{LABEL}-{mode}",
            "-enable-kvm","-cpu","host","-smp","4","-m","4G", "-device","virtio-scsi-pci,id=scsi0",
            "-device","scsi-hd,drive=hd0","-drive",f"file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0",
            "-device",DEVICE,"-net","user,hostfwd=tcp:127.0.0.1:8089-:22","-net","nic,model=virtio",
            "-display","none","-serial","none","-monitor","none","-qmp",f"unix:{qmp},server,nowait",
            "-pidfile",str(pid),"-daemonize"])
        self.record(阶段=f"{mode}客机启动",当前镜像=str(image),当前客机PID=int(pid.read_text()))
        self.authenticate(mode)

    def run(self):
        self.state["模式顺序"]=["greedy","guarded"]
        self.state["预热秒数"]=180
        self.state["固定计划"]="原滚动请求900秒；阈值4096、GC20%、紧迫10秒、额外预算16MiB、500ms同采样"
        identity=self.command(["sha256sum","build-femu/qemu-system-x86_64",
            "research/rrzc/tests/run_guarded_medium_pair.py","research/rrzc/tests/run_guarded_stream_host.py",
            "research/rrzc/tests/run_guarded_stream_guest.sh","research/rrzc/tests/stream_rrzc_test.cc",
            "research/rrzc/analyze_guarded_stream.py","research/rrzc/guarded_rounds.py"])
        (self.output/"宿主软件身份.sha256").write_text(identity)
        outcomes=[]
        for strategy in ("greedy","guarded"):
            self.start_vm(strategy,PARENT)
            self.record(阶段=f"{strategy}编译与固定业务")
            # 专用主连接由本监督器认证；密码只作为函数参数驻留内存。
            run_arm([strategy,"--medium"],password=self.password,reuse_control=True)
            run=PROJECT/"research/rrzc/results"/f"{LABEL}-{strategy}"/f"{LABEL}-{strategy}"
            self.record(阶段=f"{strategy}归档完成，独立核验中")
            outcome=analyze(run)
            (run.parent/"独立核验.json").write_text(json.dumps(outcome,ensure_ascii=False,indent=2))
            outcomes.append(outcome)
            self.record(阶段=f"{strategy}核验通过，正常关机")
            self.privileged("poweroff",60,strategy)
            for _ in range(30):
                if not Path(f"/proc/{self.state['当前客机PID']}").exists():break
                time.sleep(1)
            require(not Path(f"/proc/{self.state['当前客机PID']}").exists(),"客机未完全退出，停止下一组")
            self.state["已完成"].append(strategy)
        result=compare(*outcomes)
        (self.output/"完整对照核验.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.password=None
        self.record(阶段="两组完成、归档和核验通过",当前镜像=None,当前客机PID=None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check",action="store_true",help="仅检查，不创建镜像或启动客机")
    parser.add_argument("--detach",action="store_true",help="监督器驻留，状态写入独立目录")
    args=parser.parse_args();preflight()
    if args.check:
        print("只读前置检查通过；未创建镜像或启动客机")
        return
    password=getpass.getpass("本轮客机认证（只驻留内存）：")
    output=PROJECT/"research/rrzc/results"/LABEL;output.mkdir()
    if args.detach:
        child=os.fork()
        if child:
            print(f"监督进程已启动，PID={child}，状态={output/'状态.json'}",flush=True)
            return
        os.setsid()
        log=os.open(output/"监督进程.log",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(log,1);os.dup2(log,2)
        null=os.open(os.devnull,os.O_RDONLY);os.dup2(null,0)
    pair=GuardedPair(SimpleNamespace(label=LABEL,seconds=900,space_weight=50),password,output)
    try:
        pair.run()
    except Exception as error:
        pair.record(阶段="停止，需检查",错误=str(error))
        print("未完成；原错误、镜像和现场保留，没有自动重跑。",flush=True)
        raise SystemExit(1)


if __name__=="__main__":main()
