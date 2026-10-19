#!/usr/bin/env python3
"""只管理本轮已创建的8089联调客机，密码仅驻留内存，拒绝覆盖结果。"""
import argparse
import getpass
from pathlib import Path
import shlex
import subprocess
import pexpect

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = "/home/femu/rrzc-v1-link-20261014"
SOCKET = "/tmp/v1-shadow-link-20261014-ssh.sock"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("query", "windows", "cache-flush-check", "windows-after-flush"))
    args = parser.parse_args()
    output = ROOT / "research/rrzc/results/v1-shadow-link-20261014"
    transcript = output / f"{args.phase}.管理.log"
    if not Path(SOCKET).exists() or not output.is_dir() or transcript.exists():
        raise RuntimeError("控制连接或结果目录不符，或已有本阶段记录，拒绝执行")
    mode = args.phase if args.phase in ("query", "cache-flush-check") else "raw-window-test"
    program = DIRECTORY + "/v1_recent_guest_test"
    if args.phase in ("cache-flush-check", "windows-after-flush"):
        program += ".after-flush"
    prefix = f"{DIRECTORY}/{args.phase}"
    # 不初始化ZenFS；本轮特定NVMe设备须8GiB、未挂载，程序另核对几何和全空状态。
    command = f"""set -eu
printf 'V1认证完成\\n'
test "$(blockdev --getsize64 /dev/nvme0n1)" = 8589934592
test -z "$(lsblk -nr -o MOUNTPOINTS /dev/nvme0n1 | tr -d '[:space:]')"
test -x {program}
test ! -e {prefix}.log
printf 'mq-deadline\\n' > /sys/block/nvme0n1/queue/scheduler
cat /sys/block/nvme0n1/queue/scheduler > {prefix}.scheduler.log
nvme zns report-zones /dev/nvme0n1 -o json > {prefix}.zones.before.json
status=0
env -u ZENFS_RR_ENABLE -u ZENFS_RRZC_V1_SHADOW {program} {mode} > {prefix}.log 2>&1 || status=$?
printf '%s\\n' "$status" > {prefix}.exit
nvme zns report-zones /dev/nvme0n1 -o json > {prefix}.zones.after.json
tail -3 {prefix}.log
exit "$status"
"""
    password = getpass.getpass("隔离客机sudo密码（不记录）：")
    child = pexpect.spawn("ssh", ["-S", SOCKET, "-o", "BatchMode=yes", "-p", "8089",
                                   "-tt", "femu@127.0.0.1",
                                   "sudo -S -- bash -c " + shlex.quote(command)],
                          encoding="utf-8", timeout=20)
    result = child.expect([r"\[sudo\] password for femu:", "V1认证完成", pexpect.EOF])
    if result == 0:
        child.sendline(password)
        child.expect("V1认证完成", timeout=20)
    elif result != 1:
        child.close()
        raise RuntimeError("sudo未成功；不重试密码或继续设备操作")
    password = None
    print("认证通过；本轮" + args.phase + "检查运行中", flush=True)
    child.expect(pexpect.EOF, timeout=180)
    text = child.before
    child.close()
    transcript.write_text(text, encoding="utf-8")
    print(text, flush=True)
    # 成功或失败都保存本阶段原始记录，不能用重新运行替换失败证据。
    files = [f"{prefix}.{name}" for name in (
        "log", "exit", "scheduler.log", "zones.before.json", "zones.after.json")]
    subprocess.run(["scp", "-q", "-o", f"ControlPath={SOCKET}", "-o", "BatchMode=yes",
                    "-P", "8089", *["femu@127.0.0.1:" + f for f in files], str(output)],
                   check=True, timeout=30)
    if child.exitstatus != 0:
        raise RuntimeError("真实联调未通过；已归档，拒绝自动重跑")


if __name__ == "__main__":
    main()
