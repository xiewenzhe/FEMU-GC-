#!/usr/bin/env python3
"""新隔离子镜像同库短测；复用未改的35秒输入，失败归档并保留现场。"""
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

from run_stream_pair import Pair, require, PROJECT, IMAGE_DIR

sys.path.insert(0, str(PROJECT/"research/rrzc"))
from analyze_tail_ablation_smoke import LABEL, GUEST_LABEL, expected_software, analyze, compare

PARENT = IMAGE_DIR/"tail-ablation-build-20261015-build.qcow2"
DEVICE = "femu,devsz_mb=8192,namespaces=1,zns_num_ch=8,zns_num_lun=4,zns_num_plane=2,zns_num_blk=64,zns_flash_type=4,femu_mode=3,zns_rr_threshold=4096,zns_rr_history=on,zns_max_active=6"
DIRECTORY = f"/home/femu/rrzc-{GUEST_LABEL}-tests"
RESULT = f"/home/femu/rrzc-tail-pair-results/{GUEST_LABEL}"


def preflight():
    require(PARENT.is_file() and PARENT.resolve().parent == IMAGE_DIR, "父镜像不在获准位置")
    require(Path("/dev/kvm").exists(), "当前环境无法访问KVM")
    with socket.socket() as probe:
        require(probe.connect_ex(("127.0.0.1", 8089)) != 0, "8089已占用，不操作已有客机")
    processes = subprocess.check_output(["ps", "-C", "qemu-system-x86_64", "-o", "args="], text=True)
    require(f"file={PARENT}," not in processes, "父镜像仍在运行")
    for mode in ("keep", "bypass"):
        label = f"{LABEL}-{mode}"
        for path in (IMAGE_DIR/f"{label}.qcow2", Path(f"/tmp/{label}.pid"), Path(f"/tmp/{label}.qmp"),
                     Path(f"/tmp/{label}-ssh.sock"), PROJECT/"research/rrzc/results"/label):
            require(not path.exists(), "目标已存在，拒绝覆盖："+str(path))
    require(not (PROJECT/"research/rrzc/results"/LABEL).exists(), "监督结果目录已存在")
    fs = os.statvfs(IMAGE_DIR)
    require(fs.f_bavail*fs.f_frsize >= 12*(1 << 30), "data盘可用空间不足12GiB")


class AblationPair(Pair):
    def start_vm(self, mode, parent):
        label = f"{LABEL}-{mode}"
        image = IMAGE_DIR/f"{label}.qcow2"
        pid, qmp = Path(f"/tmp/{label}.pid"), Path(f"/tmp/{label}.qmp")
        with socket.socket() as probe:
            require(probe.connect_ex(("127.0.0.1", 8089)) != 0, "启动前8089被占用")
        require(not any(p.exists() for p in (image, pid, qmp)), "新目标已存在")
        processes = subprocess.check_output(["ps", "-C", "qemu-system-x86_64", "-o", "args="], text=True)
        require(f"file={parent}," not in processes, "父镜像仍在运行")
        self.command(["./build-femu/qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(parent), str(image)])
        self.command(["./build-femu/qemu-system-x86_64", "-name", f"FEMU-{label}",
            "-enable-kvm", "-cpu", "host", "-smp", "4", "-m", "4G", "-device", "virtio-scsi-pci,id=scsi0",
            "-device", "scsi-hd,drive=hd0", "-drive", f"file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0",
            "-device", DEVICE, "-net", "user,hostfwd=tcp:127.0.0.1:8089-:22", "-net", "nic,model=virtio",
            "-display", "none", "-serial", "none", "-monitor", "none", "-qmp", f"unix:{qmp},server,nowait",
            "-pidfile", str(pid), "-daemonize"])
        self.record(阶段=f"{mode}新客机已启动", 当前镜像=str(image), 当前客机PID=int(pid.read_text()))
        self.authenticate(mode)

    def arm(self, mode):
        output = PROJECT/"research/rrzc/results"/f"{LABEL}-{mode}"
        output.mkdir()
        expected = expected_software()
        query = "cd /home/femu/rocksdb; sha256sum "+" ".join(expected)
        before = self.ssh(query)
        require({n: d for d, n in (s.split() for s in before.splitlines())} == expected, "客机源码／库不符")
        (output/"完整源码身份.before.sha256").write_text(before)
        self.ssh(f"test ! -e {DIRECTORY} && test ! -e {RESULT} && test ! -e {RESULT}-aux && mkdir {DIRECTORY}")
        scp = ["scp", "-q", "-o", f"ControlPath={self.socket}", "-o", "BatchMode=yes", "-P", "8089"]
        sources = ["guarded_tail_test.cc", "v1_execution_smoke_test.cc", "cost_device_probe.cc", "run_guarded_tail_guest.sh"]
        self.command(scp+["research/rrzc/tests/"+n for n in sources]+[f"femu@127.0.0.1:{DIRECTORY}/"])
        self.record(阶段=f"{mode}客机测试编译")
        for name in ("guarded_tail_test", "cost_device_probe"):
            # 编译错误同样归档；绝不失败后直接启动设备业务。
            status = 0
            try:
                self.ssh(f"cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti "
                    f"-DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude {DIRECTORY}/{name}.cc librocksdb_debug.a "
                    f"-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt -o {DIRECTORY}/{name} "
                    f"> {DIRECTORY}/{name}.build.log 2>&1 || status=$?; "
                    f"printf '%s\\n' \"$status\" > {DIRECTORY}/{name}.build.exit; exit \"$status\"", 120)
            except RuntimeError:
                status = 1
            self.command(scp+[f"femu@127.0.0.1:{DIRECTORY}/{name}.build.log",
                f"femu@127.0.0.1:{DIRECTORY}/{name}.build.exit", str(output)])
            require(status == 0, "编译失败，保留客机与错误，不启动业务")
        self.ssh(f"cp {DIRECTORY}/guarded_tail_test.build.exit {DIRECTORY}/build.exit")
        self.record(阶段=f"{mode}固定准备与35秒观察")
        # 原测试的客机内部路径保持不变，但每份新镜像、宿主归档和模式均独立。
        root = "bash -c "+shlex.quote("set -e; printf 'mq-deadline\\n' > /sys/block/nvme0n1/queue/scheduler; "
            f"env -u ZENFS_MIGRATE_RESERVE {DIRECTORY}/cost_device_probe {DIRECTORY}/probe-device.log > {DIRECTORY}/probe.csv; "
            f"env ZENFS_RRZC_TAIL_ABLATION={mode} bash {DIRECTORY}/run_guarded_tail_guest.sh guarded")
        code, transcript = self.privileged(root, 300, mode, RESULT)
        (output/"客机业务输出.log").write_text(transcript)
        self.command(scp+["-r", f"femu@127.0.0.1:{DIRECTORY}", str(output)], 120)
        # 包装器失败时也尽量取回原始目录，缺目录的错误保留在监督日志。
        self.command(scp+["-r", f"femu@127.0.0.1:{RESULT}", str(output)], 120)
        (output/"完整源码身份.after.sha256").write_text(self.ssh(query))
        require(code == 0, "固定业务或数据校验失败，已归档，不重跑")
        self.record(阶段=f"{mode}独立日志核验")
        result = analyze(output/GUEST_LABEL, mode)
        (output/"独立核验.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    def run(self):
        self.state.pop("实验空间权重百分比")
        self.state["模式顺序"] = ["keep", "bypass"]
        self.state["固定输入"] = "原尾部专项：准备真实RR240MiB，尾部16MiB，冷16／热32MiB，预读3904，持续32读每秒35秒"
        self.state["唯一处理差异"] = "ZENFS_RRZC_TAIL_ABLATION=keep／bypass；均真实执行，源／目标安全复核不变"
        self.state["客机内部结果名"] = GUEST_LABEL
        identity = self.command(["sha256sum", "build-femu/qemu-system-x86_64",
            "research/rrzc/tests/run_tail_ablation_smoke_pair.py", "research/rrzc/tests/run_guarded_tail_guest.sh",
            "research/rrzc/tests/guarded_tail_test.cc", "research/rrzc/tests/v1_execution_smoke_test.cc",
            "research/rrzc/guarded_rounds.py", "research/rrzc/tail_ablation_log.py",
            "research/rrzc/analyze_tail_ablation_smoke.py"])
        (self.output/"宿主软件身份.sha256").write_text(identity)
        self.record(阶段="固定输入已登记，未启动设备业务")
        outcomes = []
        for mode in ("keep", "bypass"):
            self.start_vm(mode, PARENT)
            outcomes.append(self.arm(mode))
            self.record(阶段=f"{mode}归档和独立核验通过，正常关机")
            self.privileged("poweroff", 60, mode)
            for _ in range(30):
                if not Path(f"/proc/{self.state['当前客机PID']}").exists():
                    break
                time.sleep(1)
            require(not Path(f"/proc/{self.state['当前客机PID']}").exists(), "新客机未退出，停止下一组")
            self.state["已完成"].append(mode)
        result = compare(*outcomes)
        (self.output/"完整对照核验.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        self.password = None
        self.record(阶段="两组短测、数据校验和独立核验完成，客机均已关机", 当前客机PID=None, 当前镜像=None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只读前置检查")
    args = parser.parse_args()
    preflight()
    if args.check:
        print("只读检查通过；未创建镜像或运行业务")
        return
    password = getpass.getpass("新短测客机认证（只驻留内存）：")
    output = PROJECT/"research/rrzc/results"/LABEL
    output.mkdir()
    pair = AblationPair(SimpleNamespace(label=LABEL, seconds=35, space_weight=50), password, output)
    try:
        pair.run()
    except Exception as error:
        pair.record(阶段="停止，需检查；不自动重跑", 错误=str(error))
        raise


if __name__ == "__main__":
    main()
