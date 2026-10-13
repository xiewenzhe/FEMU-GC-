#!/usr/bin/env python3
"""串行管理两个全新隔离子镜像；密码仅驻留内存，不写入日志或命令行。"""
import argparse
import csv
import getpass
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
import pexpect

PROJECT = Path(__file__).resolve().parents[3]
IMAGE_DIR = Path("/home/data/xwz/rrzc-baselines")


class Pair:
    def __init__(self, args, password, output):
        self.args, self.password, self.output = args, password, output
        self.state = {"监督进程PID": os.getpid(), "标签": args.label,
                      "每组计划业务秒数": args.seconds, "模式顺序": ["valid", "weighted"],
                      "实验空间权重百分比": args.space_weight,
                      "阶段": "启动", "已完成": [], "当前镜像": None}
        self.socket = None

    def record(self, **fields):
        self.state.update(fields)
        self.state["更新时间UTC"] = datetime.now(timezone.utc).isoformat()
        temporary = self.output / "状态.json.tmp"
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2))
        temporary.replace(self.output / "状态.json")
        print(self.state["更新时间UTC"], self.state["阶段"], flush=True)

    def command(self, argv, timeout=60):
        result = subprocess.run(argv, cwd=PROJECT, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout)
        with (self.output / "管理命令.log").open("a") as stream:
            stream.write(json.dumps(argv, ensure_ascii=False) + "\n" + result.stdout + "\n")
        if result.returncode:
            raise RuntimeError(f"命令失败，退出{result.returncode}；详见管理命令.log")
        return result.stdout

    def ssh(self, command, timeout=30):
        return self.command(["ssh", "-S", self.socket, "-o", "BatchMode=yes", "-p", "8089",
                             "femu@127.0.0.1", command], timeout)

    def authenticate(self, mode):
        self.socket = f"/tmp/{self.args.label}-{mode}-ssh.sock"
        require(not Path(self.socket).exists(), "控制连接已存在，拒绝复用")
        argv = ["-o", "ControlMaster=yes", "-o", f"ControlPath={self.socket}",
                "-o", "ControlPersist=10800", "-o", "PreferredAuthentications=password",
                "-o", "PubkeyAuthentication=no", "-o", "StrictHostKeyChecking=yes",
                "-o", "ConnectTimeout=8", "-p", "8089", "femu@127.0.0.1", "id"]
        for attempt in range(18):
            child = pexpect.spawn("ssh", argv, encoding="utf-8", timeout=15)
            response = child.expect([r"[Pp]assword:", pexpect.EOF, pexpect.TIMEOUT])
            if response == 0:
                child.sendline(self.password)
                child.expect(pexpect.EOF, timeout=20)
                text = child.before
                child.close()
                require(child.exitstatus == 0 and "uid=1000(femu)" in text, "客机登录认证失败")
                return
            text = child.before
            child.close(force=True)
            require("Permission denied" not in text and "Host key verification failed" not in text,
                    "认证或主机密钥失败，拒绝重试密码")
            time.sleep(4)
        raise RuntimeError("客机SSH未就绪")

    def start_vm(self, mode, parent):
        with socket.socket() as probe:
            require(probe.connect_ex(("127.0.0.1", 8089)) != 0, "8089已占用，拒绝启动")
        image = IMAGE_DIR / f"{self.args.label}-{mode}.qcow2"
        pid = Path(f"/tmp/{self.args.label}-{mode}.pid")
        qmp = Path(f"/tmp/{self.args.label}-{mode}.qmp")
        require(not image.exists() and not pid.exists() and not qmp.exists(), "镜像或控制文件已存在")
        # 只允许从已停止的父镜像派生；不更改任何现有镜像。
        ps = subprocess.check_output(["ps", "-eo", "args"], text=True)
        require(not any("qemu-system-x86_64" in line.split(" ", 1)[0] and
                        f"file={parent}," in line for line in ps.splitlines()), "父镜像仍在运行")
        self.command(["./build-femu/qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
                      "-b", str(parent), str(image)])
        self.command(["./build-femu/qemu-system-x86_64", "-name", f"FEMU-{self.args.label}-{mode}",
                      "-enable-kvm", "-cpu", "host", "-smp", "4", "-m", "4G",
                      "-device", "virtio-scsi-pci,id=scsi0", "-device", "scsi-hd,drive=hd0",
                      "-drive", f"file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0",
                      "-device", "femu,devsz_mb=8192,namespaces=1,zns_num_ch=8,zns_num_lun=4,zns_num_plane=2,zns_num_blk=64,zns_flash_type=4,femu_mode=3,zns_rr_threshold=4096,zns_max_active=6",
                      "-net", "user,hostfwd=tcp:127.0.0.1:8089-:22", "-net", "nic,model=virtio",
                      "-display", "none", "-serial", "none", "-monitor", "none",
                      "-qmp", f"unix:{qmp},server,nowait", "-pidfile", str(pid), "-daemonize"])
        self.record(阶段=f"{mode}客机启动", 当前镜像=str(image), 当前客机PID=int(pid.read_text()))
        self.authenticate(mode)
        return image

    def prepare(self):
        directory = "/home/femu/rrzc-stream-tests"
        suffix = f"before-{self.args.label}"
        self.ssh(f"mkdir -p {directory}; for file in stream_rrzc_test.cc run_isolated_stream_guest.sh build.log build.exit; do if test -e {directory}/$file; then test ! -e {directory}/$file.{suffix} || exit 2; cp {directory}/$file {directory}/$file.{suffix}; fi; done")
        self.command(["scp", "-q", "-o", f"ControlPath={self.socket}", "-o", "BatchMode=yes", "-P", "8089",
                      "research/rrzc/tests/stream_rrzc_test.cc", "research/rrzc/tests/run_isolated_stream_guest.sh",
                      f"femu@127.0.0.1:{directory}/"])
        self.ssh("cd /home/femu/rocksdb; "
                 "g++ -std=c++20 -Wall -Wextra -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude "
                 f"{directory}/stream_rrzc_test.cc librocksdb_debug.a "
                 "-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt "
                 f"-o {directory}/stream_rrzc_test > {directory}/build.log 2>&1; "
                 f"status=$?; printf '%s\\n' \"$status\" > {directory}/build.exit; "
                 f"cat {directory}/build.log; exit \"$status\"", timeout=120)

    def privileged(self, command, timeout, mode, observe=None):
        child = pexpect.spawn("ssh", ["-S", self.socket, "-p", "8089", "-tt", "femu@127.0.0.1",
                                      "sudo -S " + command], encoding="utf-8", timeout=30)
        response = child.expect([r"\[sudo\] password for femu:", pexpect.EOF, pexpect.TIMEOUT])
        if response == 0:
            child.sendline(self.password)
        elif response == 1:
            child.close()
            return child.exitstatus, child.before
        started = time.monotonic()
        while True:
            response = child.expect([pexpect.EOF, pexpect.TIMEOUT], timeout=30)
            if response == 0:
                output = child.before
                child.close()
                return child.exitstatus, output
            if observe:
                try:
                    probe = self.ssh(f"tail -2 {observe}/run.stdout.log; tail -1 {observe}/states.csv; "
                                     f"printf 'ZC='; grep -c 'ZC 统计：' {observe}/zenfs.run.log; "
                                     f"printf 'RR='; grep -c 'RR 完成：' {observe}/zenfs.run.log; true", timeout=20)
                    self.record(阶段=f"{mode}运行中", 最近观测=probe)
                except Exception as exc:
                    self.record(阶段=f"{mode}运行中", 最近观测异常=str(exc))
            require(time.monotonic() - started < timeout, "运行超出安全时限，客机保留供检查")

    def run(self):
        parent = self.args.parent.resolve()
        identity = self.command(["sha256sum", "build-femu/qemu-system-x86_64",
                                 "research/rrzc/tests/run_stream_pair.py", "research/rrzc/analyze_stream.py"])
        (self.output/"宿主软件身份.sha256").write_text(identity)
        for mode in ("valid", "weighted"):
            image = self.start_vm(mode, parent)
            if self.args.prepare and mode == "valid":
                self.record(阶段="编译修正后的滚动负载")
                self.prepare()
            guest_result = f"/home/femu/rrzc-stream-results/{self.args.label}-{mode}"
            self.record(阶段=f"{mode}运行中", 客机结果目录=guest_result)
            code, output = self.privileged(f"bash /home/femu/rrzc-stream-tests/run_isolated_stream_guest.sh {guest_result} {mode} {self.args.seconds} {self.args.space_weight}",
                                           self.args.seconds + 600, mode, guest_result)
            (self.output / f"{mode}.stdout.log").write_text(output)
            arm = self.output / mode
            arm.mkdir()
            self.command(["scp", "-q", "-r", "-o", f"ControlPath={self.socket}", "-o", "BatchMode=yes",
                          "-P", "8089", f"femu@127.0.0.1:{guest_result}", str(arm)], timeout=180)
            if mode == "valid":
                build = self.output/"build"
                build.mkdir()
                self.command(["scp", "-q", "-o", f"ControlPath={self.socket}", "-o", "BatchMode=yes",
                              "-P", "8089", *[f"femu@127.0.0.1:/home/femu/rrzc-stream-tests/{name}" for name in
                                               ("stream_rrzc_test.cc", "run_isolated_stream_guest.sh", "build.log", "build.exit")],
                              str(build)], timeout=60)
            require(code == 0, "负载失败，已归档；当前客机保留，停止后续实验")
            self.record(阶段=f"{mode}归档完成")
            self.privileged("poweroff", 60, mode)
            for _ in range(30):
                if not Path(f"/proc/{self.state['当前客机PID']}").exists(): break
                time.sleep(1)
            require(not Path(f"/proc/{self.state['当前客机PID']}").exists(), "实验客机未完全停机")
            self.state["已完成"].append(mode)
            # 预试第二组继承已停机第一组的同一编译程序；正式两组同一父镜像。
            if self.args.prepare: parent = image
        self.record(阶段="两组完成并归档", 当前镜像=None, 当前客机PID=None)
        # 独立只读核验，失败也保留完整原始数据，不重新运行任何负载。
        self.record(阶段="两组原始记录核验中")
        analysis = self.command([sys.executable, "research/rrzc/analyze_stream.py",
                                 str(self.output/"valid"/f"{self.args.label}-valid"),
                                 str(self.output/"weighted"/f"{self.args.label}-weighted")], timeout=120)
        result = json.loads(analysis)
        (self.output/"完整对照核验.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        arms = {r["配置"]["策略"]: r for r in result["运行"]}
        with (self.output/"指标汇总.csv").open("x") as stream:
            writer = csv.writer(stream)
            mechanism = "等权机制" if self.args.space_weight == 50 else f"空间{self.args.space_weight}/读风险{100-self.args.space_weight}机制"
            writer.writerow(["统计范围", "指标", "有效量基线", mechanism, "单位"])
            for section in ("全程维护", "预热后维护"):
                for key in arms["valid"][section]:
                    writer.writerow([section, key, arms["valid"][section][key], arms["weighted"][section][key],
                                     "字节" if "字节" in key else "次数"])
            for section in ("预热后API延迟", "预热后排程到API完成响应延迟", "预热后排程迟到"):
                for op in arms["valid"][section]:
                    for metric in ("样本数", "平均微秒", "p50微秒", "p95微秒", "p99微秒", "最大微秒"):
                        writer.writerow([section, op+"/"+metric, arms["valid"][section][op][metric],
                                         arms["weighted"][section][op][metric], "次数" if metric == "样本数" else "微秒"])
        self.record(阶段="两组完成、归档和核验通过")


def require(value, message):
    if not value: raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--parent", required=True, type=Path)
    parser.add_argument("--seconds", required=True, type=int)
    parser.add_argument("--space-weight", type=int, choices=(50, 80), default=50)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--detach", action="store_true")
    args = parser.parse_args()
    require(re.fullmatch(r"stream-[a-z0-9-]+", args.label), "标签非法")
    require(args.parent.resolve().parent == IMAGE_DIR and args.parent.is_file(), "父镜像不在获准目录")
    require(60 <= args.seconds <= 14400 and args.seconds % 30 == 0, "时长非法")
    output = PROJECT / "research/rrzc/results" / args.label
    require(not output.exists(), "结果目录已存在，拒绝覆盖")
    password = getpass.getpass("隔离客机密码（只驻留内存，不记录）：")
    output.mkdir()
    if args.detach:
        pid = os.fork()
        if pid:
            print(f"监督进程已启动，PID={pid}，状态记录={output/'状态.json'}", flush=True)
            return
        os.setsid()
        log = os.open(output / "监督进程.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.dup2(log, 1); os.dup2(log, 2)
        null = os.open(os.devnull, os.O_RDONLY); os.dup2(null, 0)
    pair = Pair(args, password, output)
    try:
        pair.run()
    except Exception as exc:
        pair.record(阶段="停止，需检查", 错误=str(exc))
        print("未完成；没有自动重跑、格式化既有设备或丢弃日志。", flush=True)
        sys.exit(1)


if __name__ == "__main__": main()
