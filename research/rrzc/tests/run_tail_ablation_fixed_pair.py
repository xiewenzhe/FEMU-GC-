#!/usr/bin/env python3
"""同一修复父镜像串行900秒配对；失败采证归档，绝不重跑或自动杀业务。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import pexpect

from run_stream_pair import Pair, require, PROJECT, IMAGE_DIR
from run_tail_ablation_smoke_pair import DEVICE

sys.path.insert(0, str(PROJECT/"research/rrzc"))
from analyze_tail_ablation_fixed import LABEL, GUEST_LABEL, expected_software, analyze, compare
from ablation_sampling import RULE

PORT = "8090"
PARENT = IMAGE_DIR/"reopen-lock-build-20261015.qcow2"
DIRECTORY = f"/home/femu/rrzc-{GUEST_LABEL}-tests"
RESULT = f"/home/femu/rrzc-stream-results/{GUEST_LABEL}"


def running_parent(parent=PARENT):
    listing = subprocess.run(["ps","-C","qemu-system-x86_64","-o","args="],
                             text=True,stdout=subprocess.PIPE,check=False).stdout
    return f"file={parent}," in listing


def preflight():
    require(PARENT.is_file() and PARENT.resolve().parent == IMAGE_DIR, "父镜像不在获准目录")
    require(Path("/dev/kvm").exists() and not running_parent(), "KVM不可用或修复父镜像仍在运行")
    with socket.socket() as probe:
        require(probe.connect_ex(("127.0.0.1",int(PORT))) != 0, "8090被占用，不操作已有客机")
    for mode in ("keep","bypass"):
        label = f"{LABEL}-{mode}"
        for path in (IMAGE_DIR/f"{label}.qcow2",Path(f"/tmp/{label}.pid"),Path(f"/tmp/{label}.qmp"),
                     Path(f"/tmp/{label}-ssh.sock"),PROJECT/"research/rrzc/results"/label):
            require(not path.exists(), "目标已存在，拒绝覆盖："+str(path))
    require(not (PROJECT/"research/rrzc/results"/LABEL).exists(), "监督目录已存在")
    for path, minimum in ((IMAGE_DIR,12<<30),(PROJECT,3<<30)):
        fs = os.statvfs(path)
        require(fs.f_bavail*fs.f_frsize >= minimum, "镜像或归档盘余量不足")
    expected_software()


class FixedPair(Pair):
    # 新独立功能入口可指定标签；原配对默认路径不变。
    label_prefix = LABEL
    parent_image = PARENT
    port = PORT
    def ssh(self, command, timeout=30):
        return self.command(["ssh","-S",self.socket,"-o","BatchMode=yes","-p",self.port,
                             "femu@127.0.0.1",command],timeout)

    def root(self, command, timeout=45):
        # 只让认证和短诊断使用PTY，长业务由独立会话运行。
        root = "sudo -S -- bash -c "+shlex.quote("printf '认证已完成\\n'; "+command)
        child = pexpect.spawn("ssh",["-S",self.socket,"-o","BatchMode=yes","-p",self.port,
            "-tt","femu@127.0.0.1",root],encoding="utf-8",timeout=20)
        response = child.expect([r"\[sudo\] password for femu:","认证已完成",pexpect.EOF])
        if response == 0:
            child.sendline(self.password)
            child.expect("认证已完成")
        else:
            require(response == 1,"sudo认证未成功")
        child.expect(pexpect.EOF,timeout=timeout)
        output = child.before
        child.close()
        require(child.exitstatus == 0,"短管理操作失败："+output[-1000:])
        return output

    def start(self, mode):
        label = f"{self.label_prefix}-{mode}"
        image = IMAGE_DIR/f"{label}.qcow2"
        self.qmp = Path(f"/tmp/{label}.qmp")
        pid = Path(f"/tmp/{label}.pid")
        self.socket = f"/tmp/{label}-ssh.sock"
        require(not running_parent(self.parent_image) and not any(p.exists() for p in (image,pid,self.qmp,Path(self.socket))),
                "启动目标或父镜像状态变化")
        with socket.socket() as probe:
            require(probe.connect_ex(("127.0.0.1",int(self.port))) != 0,"实验端口启动前被占用")
        self.command(["./build-femu/qemu-img","create","-f","qcow2","-F","qcow2","-b",str(self.parent_image),str(image)])
        if hasattr(self, 'image_created'):
            self.image_created(image, self.parent_image)
        self.command(["./build-femu/qemu-system-x86_64","-name",f"FEMU-{label}",
            "-enable-kvm","-cpu","host","-smp","4","-m","4G","-device","virtio-scsi-pci,id=scsi0",
            "-device","scsi-hd,drive=hd0","-drive",f"file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0",
            "-device",DEVICE,"-net",f"user,hostfwd=tcp:127.0.0.1:{self.port}-:22","-net","nic,model=virtio",
            "-display","none","-serial","none","-monitor","none","-qmp",f"unix:{self.qmp},server,nowait",
            "-pidfile",str(pid),"-daemonize"])
        self.record(阶段=f"{mode}新客机启动",当前镜像=str(image),当前客机PID=int(pid.read_text()))
        argv = ["-o","ControlMaster=yes","-o",f"ControlPath={self.socket}","-o","ControlPersist=3600",
            "-o","PreferredAuthentications=password","-o","PubkeyAuthentication=no","-o","StrictHostKeyChecking=yes",
            "-o","HostKeyAlias=[127.0.0.1]:8089","-o","ConnectTimeout=8","-p",self.port,"femu@127.0.0.1","id"]
        for _ in range(18):
            child = pexpect.spawn("ssh",argv,encoding="utf-8",timeout=15)
            response = child.expect([r"[Pp]assword:",pexpect.EOF,pexpect.TIMEOUT])
            if response == 0:
                child.sendline(self.password); child.expect(pexpect.EOF)
                output = child.before; child.close()
                require(child.exitstatus == 0 and "uid=1000(femu)" in output,"认证失败，不重试密码")
                return
            output = child.before; child.close(force=True)
            require("Permission denied" not in output and "Host key verification failed" not in output,"认证或主机密钥不符")
            time.sleep(4)
        raise RuntimeError("新客机SSH未就绪")

    def archive(self, output, scp, query):
        # 失败路径也逐项尝试归档；一项失败不阻止其他证据取回。
        errors = []
        for remote in (DIRECTORY,RESULT):
            try:
                self.command(scp+["-r",f"femu@127.0.0.1:{remote}",str(output)],120)
            except Exception as error:
                errors.append(str(error)+"；"+remote)
        try:
            (output/"完整源码身份.after.sha256").write_text(self.ssh(query))
        except Exception as error:
            errors.append(str(error))
        (output/"归档状态.json").write_text(json.dumps(dict(完整=not errors,错误=errors),ensure_ascii=False,indent=2))
        return errors

    def arm(self, mode):
        output = PROJECT/"research/rrzc/results"/f"{LABEL}-{mode}"
        output.mkdir()
        scp = ["scp","-q","-o",f"ControlPath={self.socket}","-o","BatchMode=yes","-P",PORT]
        expected = expected_software()
        query = "cd /home/femu/rocksdb; sha256sum "+" ".join(expected)
        identity = self.ssh(query)
        require({n:d for d,n in (r.split() for r in identity.splitlines())} == expected,"实际核心源／库不符")
        (output/"完整源码身份.before.sha256").write_text(identity)
        self.ssh(f"test ! -e {DIRECTORY} && test ! -e {RESULT} && test ! -e {RESULT}-aux && mkdir {DIRECTORY}")
        sources = ["stream_rrzc_test.cc","cost_device_probe.cc","run_guarded_stream_guest.sh",
                   "run_tail_ablation_fixed_guest.sh","capture_fixed_failure.py"]
        self.command(scp+["research/rrzc/tests/"+name for name in sources]+[f"femu@127.0.0.1:{DIRECTORY}/"])
        try:
            self.record(阶段=f"{mode}核对固定库并编译原业务程序")
            for name in ("stream_rrzc_test","cost_device_probe"):
                self.ssh(f"cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti "
                    f"-DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude {DIRECTORY}/{name}.cc librocksdb_debug.a "
                    f"-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt -o {DIRECTORY}/{name} "
                    f"> {DIRECTORY}/{name}.build.log 2>&1 || status=$?; "
                    f"printf '%s\\n' \"$status\" > {DIRECTORY}/{name}.build.exit; exit \"$status\"",120)
            self.ssh(f"cp {DIRECTORY}/stream_rrzc_test.build.exit {DIRECTORY}/build.exit")
            (output/"诊断工具.log").write_text(self.ssh("command -v gdb || true"))
            self.root(f"set -e; nohup setsid bash {DIRECTORY}/run_tail_ablation_fixed_guest.sh {mode} "
                f"< /dev/null > {DIRECTORY}/launcher.log 2>&1 & printf '%s\\n' \"$!\" > {DIRECTORY}/launcher.pid")
            self.record(阶段=f"{mode}固定900秒业务运行，已脱离SSH会话")
            started = time.monotonic()
            complete = False
            while time.monotonic()-started < 1260:
                progress = self.ssh(f"if test -f {DIRECTORY}/finished; then printf '包装已结束\\n'; fi; "
                    f"test ! -f {RESULT}/run.stdout.log || tail -1 {RESULT}/run.stdout.log; "
                    f"test ! -f {RESULT}/states.csv || tail -1 {RESULT}/states.csv; true")
                self.record(阶段=f"{mode}业务／校验中",最近观测=progress)
                if "包装已结束" in progress:
                    complete = True
                    break
                time.sleep(15)
            require(complete,"固定观察超时，准备采证并保留业务")
            require(self.ssh(f"cat {DIRECTORY}/launcher.exit").strip() == "0","包装或业务失败")
        except Exception:
            self.record(阶段=f"{mode}失败采证，未终止业务")
            try:
                (output/"失败线程现场.log").write_text(self.root(f"python3 {DIRECTORY}/capture_fixed_failure.py",60))
            except Exception as error:
                (output/"采证错误.log").write_text(str(error))
            self.archive(output,scp,query)
            raise
        require(not self.archive(output,scp,query),"归档不完整，保留客机")
        wrapper = output/Path(DIRECTORY).name
        (output/"launcher.exit").write_bytes((wrapper/"launcher.exit").read_bytes())
        require(identity == (output/"完整源码身份.after.sha256").read_text(),"核心源／库运行期间改变")
        self.record(阶段=f"{mode}已归档，核验选择、逐文件搬运和延迟")
        outcome = analyze(output/GUEST_LABEL,mode)
        (output/"独立核验.json").write_text(json.dumps(outcome,ensure_ascii=False,indent=2))
        return outcome

    def shutdown(self):
        pid = self.state["当前客机PID"]
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        require(f"file={self.state['当前镜像']},if=none,aio=native,cache=none,format=qcow2,id=hd0".encode() in args,
                "关机PID与本轮镜像不符")
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as control:
            control.settimeout(10); control.connect(str(self.qmp))
            stream = control.makefile("rwb",buffering=0)
            greeting = json.loads(stream.readline()); require("QMP" in greeting,"非QMP控制口")
            for command in ("qmp_capabilities","system_powerdown"):
                stream.write((json.dumps({"execute":command,"id":command})+"\n").encode())
                while True:
                    reply = json.loads(stream.readline())
                    if reply.get("id") == command:
                        require("return" in reply,"正常关机请求失败")
                        break
        for _ in range(30):
            if not Path(f"/proc/{pid}").exists():
                return
            time.sleep(1)
        raise RuntimeError("新客机未正常退出，不强制终止或启动下一组")

    def run(self):
        self.state.pop("实验空间权重百分比")
        plan = dict(标签=LABEL,父镜像=str(PARENT),设备参数=DEVICE,模式顺序=["keep","bypass"],
            每组业务秒数=900,预热秒数=180,唯一处理差异="仅尾部准入keep／bypass，两组均execute",
            RR阈值=4096,GC启动比例=20,紧迫界限秒数=10,额外有效预算MiB=16,回收资格MiB=192,
            核心身份=expected_software(),采样规则=RULE,目标采样毫秒=500,覆盖界限秒数=2,
            超时秒数=1260,无自动重跑=True,失败处理="采线程现场和原始记录，保留客机及业务，不自动杀进程",
            研究问题="尾部准入是否降低总迁移、重复复制与空间损失；同时检查错失改选机会",
            成功判据="固定请求、源码身份、数据及维护账目核验，不要求正收益；覆盖不足时预测后果未知")
        (self.output/"预注册计划.json").write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        snapshot = self.output/"本轮源码"; snapshot.mkdir()
        # 保存本轮全部分析和测试脚本的身份，旧结果仍由旧入口解释。
        sources = sorted((PROJECT/"research/rrzc").glob("*.py"))
        scripts = sorted((PROJECT/"research/rrzc/tests").glob("*.py"))
        scripts += [PROJECT/"research/rrzc/tests"/n for n in ("stream_rrzc_test.cc","cost_device_probe.cc",
                    "run_guarded_stream_guest.sh","run_tail_ablation_fixed_guest.sh")]
        self.command(["cp",*map(str,sources),str(snapshot)])
        (snapshot/"tests").mkdir()
        self.command(["cp",*map(str,scripts),str(snapshot/"tests")])
        (self.output/"宿主软件身份.sha256").write_text(self.command(["sha256sum","build-femu/qemu-system-x86_64",
            *map(str,sources+scripts)]))
        self.record(阶段="固定实验和评价规则已登记",模式顺序=["keep","bypass"],固定计划=plan)
        outcomes = []
        for mode in ("keep","bypass"):
            self.start(mode)
            outcomes.append(self.arm(mode))
            self.record(阶段=f"{mode}归档及独立核验通过，正常关机")
            self.shutdown()
            self.state["已完成"].append(mode)
            self.record(阶段=f"{mode}已完成并关机",当前客机PID=None,当前镜像=None)
        result = compare(*outcomes)
        (self.output/"完整对照核验.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.password = None
        self.record(阶段="两组900秒完成、数据和独立对照核验通过，客机均已关机")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check",action="store_true",help="只读前置检查")
    parser.add_argument("--detach",action="store_true",help="后台监督，状态持续写入独立目录")
    options = parser.parse_args()
    preflight()
    if options.check:
        print("只读前置检查通过，未创建镜像或运行业务")
        return
    password = getpass.getpass("新配对认证（仅驻留内存）：")
    output = PROJECT/"research/rrzc/results"/LABEL; output.mkdir()
    if options.detach:
        child = os.fork()
        if child:
            print(f"监督进程PID={child}，状态={output/'状态.json'}",flush=True)
            return
        os.setsid()
        log = os.open(output/"监督进程.log",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(log,1); os.dup2(log,2)
        null = os.open(os.devnull,os.O_RDONLY); os.dup2(null,0)
    pair = FixedPair(SimpleNamespace(label=LABEL,seconds=900,space_weight=50),password,output)
    try:
        pair.run()
    except Exception as error:
        pair.record(阶段="停止，需检查；没有自动重跑或关闭当前客机",错误=str(error))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
