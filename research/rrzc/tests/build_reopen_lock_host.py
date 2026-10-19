#!/usr/bin/env python3
"""仅8090新子镜像修复构建；保留8080及失败8089，不访问其设备。"""
import getpass
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import pexpect

from run_stream_pair import Pair, require, PROJECT, IMAGE_DIR
from run_tail_ablation_smoke_pair import DEVICE

LABEL = "reopen-lock-build-20261015"
DIRECTORY = "/home/femu/rrzc-"+LABEL
PARENT = IMAGE_DIR/"tail-ablation-build-20261015-build.qcow2"
PORT = "8090"


class BuildPair(Pair):
    def ssh(self, command, timeout=30):
        return self.command(["ssh", "-S", self.socket, "-o", "BatchMode=yes", "-p", PORT,
                             "femu@127.0.0.1", command], timeout)

    def start(self):
        image = IMAGE_DIR/f"{LABEL}.qcow2"
        pid, qmp = Path(f"/tmp/{LABEL}.pid"), Path(f"/tmp/{LABEL}.qmp")
        require(PARENT.is_file() and not any(p.exists() for p in (image,pid,qmp)), "父镜像或新目标不符")
        with socket.socket() as probe:
            require(probe.connect_ex(("127.0.0.1", int(PORT))) != 0, "8090已占用")
        processes = subprocess.check_output(["ps","-C","qemu-system-x86_64","-o","args="],text=True)
        require(f"file={PARENT}," not in processes, "构建父镜像仍在运行")
        self.command(["./build-femu/qemu-img","create","-f","qcow2","-F","qcow2","-b",str(PARENT),str(image)])
        self.command(["./build-femu/qemu-system-x86_64","-name",f"FEMU-{LABEL}",
            "-enable-kvm","-cpu","host","-smp","4","-m","4G","-device","virtio-scsi-pci,id=scsi0",
            "-device","scsi-hd,drive=hd0","-drive",f"file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0",
            "-device",DEVICE,"-net",f"user,hostfwd=tcp:127.0.0.1:{PORT}-:22","-net","nic,model=virtio",
            "-display","none","-serial","none","-monitor","none","-qmp",f"unix:{qmp},server,nowait",
            "-pidfile",str(pid),"-daemonize"])
        self.record(阶段="8090新构建客机启动",当前镜像=str(image),当前客机PID=int(pid.read_text()))
        self.socket = f"/tmp/{LABEL}-ssh.sock"
        require(not Path(self.socket).exists(), "新控制连接已存在")
        args = ["-o","ControlMaster=yes","-o",f"ControlPath={self.socket}","-o","ControlPersist=3600",
            "-o","PreferredAuthentications=password","-o","PubkeyAuthentication=no","-o","StrictHostKeyChecking=yes",
            "-o","HostKeyAlias=[127.0.0.1]:8089","-o","ConnectTimeout=8","-p",PORT,"femu@127.0.0.1","id"]
        for _ in range(18):
            child = pexpect.spawn("ssh",args,encoding="utf-8",timeout=15)
            response = child.expect([r"[Pp]assword:",pexpect.EOF,pexpect.TIMEOUT])
            if response == 0:
                child.sendline(self.password); child.expect(pexpect.EOF)
                text = child.before; child.close()
                require(child.exitstatus==0 and "uid=1000(femu)" in text,"新客机认证失败，不重试密码")
                return
            text=child.before; child.close(force=True)
            require("Permission denied" not in text and "Host key verification failed" not in text,"认证或主机密钥不符")
            time.sleep(4)
        raise RuntimeError("新构建客机SSH未就绪")

    def run(self):
        self.state.pop("实验空间权重百分比")
        self.state["模式顺序"]=["仅修复构建和内存回归，不运行业务"]
        self.start()
        previous=PROJECT/"research/rrzc/results/tail-ablation-build-20261015/rrzc-tail-ablation-build-20261015"
        expected={"plugin/zenfs/"+n:d for d,n in (r.split() for r in (previous/"source.after.sha256").read_text().splitlines())}
        expected["librocksdb_debug.a"]=next(d for d,n in
            (r.split() for r in (previous/"binaries.sha256").read_text().splitlines()) if n=="librocksdb_debug.a")
        identity=self.ssh("cd /home/femu/rocksdb; sha256sum "+" ".join(expected))
        require({n:d for d,n in (r.split() for r in identity.splitlines())}==expected,"继承源／库身份不符")
        (self.output/"客机构建前身份.sha256").write_text(identity)
        self.ssh(f"test ! -e {DIRECTORY} && mkdir {DIRECTORY}")
        scp=["scp","-q","-o",f"ControlPath={self.socket}","-o","BatchMode=yes","-P",PORT]
        self.command(scp+["research/rrzc/zenfs-reopen-lock.patch","research/rrzc/tests/build_reopen_lock_guest.sh",
            "research/rrzc/tests/test_reopen_lock.cc",str(previous/"source.after.sha256"),f"femu@127.0.0.1:{DIRECTORY}/"])
        self.ssh(f"mv {DIRECTORY}/source.after.sha256 {DIRECTORY}/source.base.sha256")
        self.record(阶段="新库构建和真实函数内存回归")
        success=True
        try:
            self.ssh(f"bash {DIRECTORY}/build_reopen_lock_guest.sh",600)
        except RuntimeError:
            success=False
        self.command(scp+["-r",f"femu@127.0.0.1:{DIRECTORY}",str(self.output)],120)
        require(success,"构建／回归失败，原日志已归档，不重跑")
        archive=self.output/Path(DIRECTORY).name
        for name in ("library-build.exit","old/build.exit","old/run.exit","fixed/build.exit","fixed/run.exit"):
            require((archive/name).read_text().strip()=="0","构建或回归退出失败")
        before={n:d for d,n in (r.split() for r in (archive/"source.before.sha256").read_text().splitlines())}
        after={n:d for d,n in (r.split() for r in (archive/"source.after.sha256").read_text().splitlines())}
        require(set(before)==set(after) and [n for n in before if before[n]!=after[n]]==["fs/fs_zenfs.cc"],"核心修改超出单一接线源")
        digest=self.command(["sha256sum","build-femu/rrzc/reopen-lock-work/fs/fs_zenfs.cc"]).split()[0]
        require(after["fs/fs_zenfs.cc"]==digest,"客机和本地修复源不一致")
        (self.output/"构建核验.json").write_text(json.dumps(dict(核验通过=True,修改文件=["fs/fs_zenfs.cc"],
            其他核心文件保持原哈希=True,设备读写=False,旧失败现场保留=True),ensure_ascii=False,indent=2))
        self.password=None
        # 保留已核验新客机供下一项短测；不自动运行NVMe测试或关闭旧现场。
        self.record(阶段="新库与内存回归通过，8090客机留待设备短测",已完成=["构建和内存回归"])


def main():
    output=PROJECT/"research/rrzc/results"/LABEL
    require(not output.exists(),"本轮构建目录已存在，拒绝覆盖")
    password=getpass.getpass("新8090构建客机认证（仅驻留内存）：")
    output.mkdir()
    pair=BuildPair(SimpleNamespace(label=LABEL,seconds=0,space_weight=50),password,output)
    try:
        pair.run()
    except Exception as error:
        pair.record(阶段="停止，需检查",错误=str(error))
        raise


if __name__=="__main__":main()
