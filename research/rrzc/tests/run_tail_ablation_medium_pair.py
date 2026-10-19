#!/usr/bin/env python3
"""预注册900秒尾部消融；串行新镜像，归档核验后关机，不自动重跑。"""
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
from run_tail_ablation_smoke_pair import PARENT, DEVICE

sys.path.insert(0, str(PROJECT/"research/rrzc"))
from analyze_tail_ablation_medium import LABEL, GUEST_LABEL, analyze, compare
from ablation_sampling import RULE


def preflight():
    require(PARENT.is_file() and PARENT.resolve().parent == IMAGE_DIR, "构建父镜像不在获准位置")
    require(Path("/dev/kvm").exists(), "当前环境无法访问KVM")
    with socket.socket() as probe:
        require(probe.connect_ex(("127.0.0.1", 8089)) != 0, "8089已占用，不操作已有客机")
    processes = subprocess.check_output(["ps", "-C", "qemu-system-x86_64", "-o", "args="], text=True)
    require(f"file={PARENT}," not in processes, "构建父镜像仍在运行")
    for mode in ("keep", "bypass"):
        label = f"{LABEL}-{mode}"
        for path in (IMAGE_DIR/f"{label}.qcow2", Path(f"/tmp/{label}.pid"), Path(f"/tmp/{label}.qmp"),
                     Path(f"/tmp/{label}-ssh.sock"), PROJECT/"research/rrzc/results"/label):
            require(not path.exists(), "本轮目标已存在，拒绝覆盖："+str(path))
    require(not (PROJECT/"research/rrzc/results"/LABEL).exists(), "监督目录已存在")
    fs = os.statvfs(IMAGE_DIR)
    require(fs.f_bavail*fs.f_frsize >= 12*(1 << 30), "data盘可用空间不足12GiB")


class MediumPair(Pair):
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

    def run(self):
        self.state.pop("实验空间权重百分比")
        self.state["模式顺序"] = ["keep", "bypass"]
        self.state["预热秒数"] = 180
        plan = dict(标签=LABEL,父镜像=str(PARENT),设备参数=DEVICE,模式顺序=["keep", "bypass"],
            每组业务秒数=900,预热秒数=180,唯一处理差异="仅尾部准入keep／bypass；两组均execute",
            原业务源SHA256="277ce66cf22a0a665eec2cf23d504e83058165971df6d5c5c3911b74868d276e",
            RR阈值=4096,GC启动比例=20,紧迫界限秒数=10,额外有效预算MiB=16,回收资格MiB=192,
            采样规则=RULE,目标采样毫秒=500,每批完整Zone数=32,预注册覆盖界限秒数=2,
            恰好1800批不是判据=True,覆盖不达标处理="报告空档和真实业务指标；预测命中／误报不判定，不插值或自动重跑",
            成功判据="固定全部请求、源码身份、数据校验、选择和维护总账通过；采样协议及覆盖质量分别报告，不要求收益为正")
        (self.output/"预注册计划.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2))
        sources = ["build-femu/qemu-system-x86_64", "research/rrzc/tests/run_tail_ablation_medium_pair.py",
            "research/rrzc/tests/run_guarded_stream_host.py", "research/rrzc/tests/run_guarded_stream_guest.sh",
            "research/rrzc/tests/stream_rrzc_test.cc", "research/rrzc/tests/cost_device_probe.cc",
            "research/rrzc/tests/run_stream_pair.py", "research/rrzc/tests/run_tail_ablation_smoke_pair.py",
            "research/rrzc/analyze_tail_ablation_medium.py", "research/rrzc/analyze_guarded_stream.py",
            "research/rrzc/analyze_tail_ablation_smoke.py", "research/rrzc/guarded_rounds.py",
            "research/rrzc/tail_ablation_log.py", "research/rrzc/ablation_sampling.py"]
        identity = self.command(["sha256sum", *sources])
        (self.output/"宿主软件身份.sha256").write_text(identity)
        # 业务开始前保留所用脚本，之后不编辑本轮核验源码或阈值。
        snapshot = self.output/"本轮源码"
        snapshot.mkdir()
        self.command(["cp", *sources[1:], str(snapshot)])
        self.record(阶段="固定请求、采样和评价规则已登记，未启动设备业务", 固定计划=plan)
        outcomes = []
        for mode in ("keep", "bypass"):
            self.start_vm(mode, PARENT)
            self.record(阶段=f"{mode}编译与固定900秒业务")
            run_arm(["guarded", "--medium", "--tail-ablation", mode], password=self.password, reuse_control=True)
            run = PROJECT/"research/rrzc/results"/f"{LABEL}-{mode}"/GUEST_LABEL
            self.record(阶段=f"{mode}已归档，独立核验中")
            outcome = analyze(run, mode)
            (run.parent/"独立核验.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
            outcomes.append(outcome)
            self.record(阶段=f"{mode}业务及数据核验通过，正常关机", 最近采样核验=outcome["采样完整性核验"])
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
        self.record(阶段="两组固定900秒完成、归档核验通过，客机均已关机", 当前镜像=None, 当前客机PID=None,
                    两组逐块覆盖均达标=result["两组逐块覆盖均达标"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只读前置检查")
    parser.add_argument("--detach", action="store_true", help="监督器后台驻留，状态写入独立目录")
    args = parser.parse_args()
    preflight()
    if args.check:
        print("只读前置检查通过；未创建镜像或启动业务")
        return
    password = getpass.getpass("本轮新客机认证（只驻留内存）：")
    output = PROJECT/"research/rrzc/results"/LABEL
    output.mkdir()
    if args.detach:
        child = os.fork()
        if child:
            print(f"监督进程已启动，PID={child}，状态={output/'状态.json'}", flush=True)
            return
        os.setsid()
        log = os.open(output/"监督进程.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.dup2(log, 1); os.dup2(log, 2)
        null = os.open(os.devnull, os.O_RDONLY); os.dup2(null, 0)
    pair = MediumPair(SimpleNamespace(label=LABEL, seconds=900, space_weight=50), password, output)
    try:
        pair.run()
    except Exception as error:
        pair.record(阶段="停止，需检查；没有自动重跑", 错误=str(error))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
