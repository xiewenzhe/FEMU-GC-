#!/usr/bin/env python3
"""本轮单客机180秒影子短测管理；只连新子镜像，不管理原8080实例。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
import pexpect

PROJECT = Path(__file__).resolve().parents[3]


def require(ok, why):
    if not ok:
        raise RuntimeError(why)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label",choices=("v1-shadow-zc-20261014","v1-trajectory-20261014","v1-victim-cases-20261014"),default="v1-shadow-zc-20261014")
    args=parser.parse_args()
    LABEL=args.label
    SOCKET=f"/tmp/{LABEL}-ssh.sock"
    DIRECTORY="/home/femu/rrzc-v1-shadow-stream-tests" if LABEL=="v1-shadow-zc-20261014" else f"/home/femu/rrzc-{LABEL}-tests"
    GUEST_RESULT=f"/home/femu/rrzc-stream-results/{LABEL}"
    sample_ms=0 if LABEL=="v1-shadow-zc-20261014" else 500
    output = PROJECT / "research/rrzc/results" / LABEL
    require(not output.exists() and not Path(SOCKET).exists(), "本轮目录或控制连接已存在，拒绝复用")
    pid = int(Path(f"/tmp/{LABEL}.pid").read_text())
    command_line = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    require(f"file=/home/data/xwz/rrzc-baselines/{LABEL}.qcow2,if=none,aio=native,cache=none,format=qcow2,id=hd0".encode() in command_line,
            "本轮PID不对应指定子镜像，停止")
    output.mkdir()
    password = getpass.getpass("本轮隔离客机密码（只驻留内存）：")
    child = pexpect.spawn("ssh", ["-o", "ControlMaster=yes", "-o", f"ControlPath={SOCKET}",
        "-o", "ControlPersist=900", "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-p", "8089", "femu@127.0.0.1", "id"],
        encoding="utf-8", timeout=20)
    child.expect(r"[Pp]assword:")
    child.sendline(password)
    child.expect(pexpect.EOF, timeout=20)
    login_output = child.before
    child.close()
    require(child.exitstatus == 0 and "uid=1000(femu)" in login_output, "客机认证失败；不重试密码")

    def command(argv, timeout=60):
        result = subprocess.run(argv, cwd=PROJECT, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout)
        with (output/"管理命令.log").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(argv, ensure_ascii=False)+"\n"+result.stdout+"\n")
        require(result.returncode == 0, "命令失败，保留管理命令.log，不继续负载")
        return result.stdout

    def ssh(text, timeout=60):
        return command(["ssh", "-S", SOCKET, "-o", "BatchMode=yes", "-p", "8089", "femu@127.0.0.1", text], timeout)

    print("客机认证通过，核对源码并编译短测程序", flush=True)
    ssh(f"test ! -e {DIRECTORY} && mkdir {DIRECTORY}")
    identity = ssh("cd /home/femu/rocksdb; sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h "
                   "plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc "
                   "plugin/zenfs/fs/zbdlib_zenfs.h plugin/zenfs/fs/rrzc_recent_log.h plugin/zenfs/fs/rrzc_urgency_selector.h librocksdb_debug.a")
    # 库和源码须仍是上一轮已验证编译后的状态，不依靠继承镜像名称猜测。
    previous = PROJECT/"research/rrzc/results/v1-shadow-link-20261014"
    expected = dict(line.split(maxsplit=1)[::-1] for line in (previous/"source.after-fix.sha256").read_text().splitlines())
    expected.update(dict(line.split(maxsplit=1)[::-1] for line in (previous/"binaries.sha256").read_text().splitlines()
                         if line.endswith("librocksdb_debug.a")))
    actual = dict(line.split(maxsplit=1)[::-1] for line in identity.splitlines())
    require(all(actual.get(name) == digest for name,digest in expected.items()), "客机源码/静态库不匹配联调基底")
    (output/"客机编译前身份.sha256").write_text(identity, encoding="utf-8")
    command(["scp", "-q", "-o", f"ControlPath={SOCKET}", "-o", "BatchMode=yes", "-P", "8089",
             "research/rrzc/tests/stream_rrzc_test.cc", "research/rrzc/tests/run_v1_shadow_stream_guest.sh",
             f"femu@127.0.0.1:{DIRECTORY}/"])
    ssh(f"cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX "
        f"-I. -Iinclude {DIRECTORY}/stream_rrzc_test.cc librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt "
        f"-o {DIRECTORY}/stream_rrzc_test > {DIRECTORY}/build.log 2>&1 || status=$?; "
        f"printf '%s\\n' \"$status\" > {DIRECTORY}/build.exit; cat {DIRECTORY}/build.log; exit \"$status\"",120)
    print("编译通过；开始180秒业务及独立只读校验", flush=True)
    root_command = "sudo -S -- bash -c " + shlex.quote(f"printf 'V1认证完成\\n'; bash {DIRECTORY}/run_v1_shadow_stream_guest.sh {LABEL} {sample_ms}")
    child = pexpect.spawn("ssh", ["-S", SOCKET, "-o", "BatchMode=yes", "-p", "8089", "-tt", "femu@127.0.0.1", root_command],
                          encoding="utf-8", timeout=20)
    response = child.expect([r"\[sudo\] password for femu:", "V1认证完成", pexpect.EOF])
    if response == 0:
        child.sendline(password)
        child.expect("V1认证完成",timeout=20)
    else:
        require(response == 1,"sudo认证未成功，不继续")
    password = None
    started = time.monotonic()
    while True:
        response = child.expect([pexpect.EOF, pexpect.TIMEOUT], timeout=30)
        if response == 0:
            transcript = child.before
            child.close()
            break
        require(time.monotonic()-started < 540, "超过短测安全时限；保留客机，不自动杀进程或重跑")
        observation = ssh(f"tail -1 {GUEST_RESULT}/run.stdout.log; "
                          f"test ! -e {GUEST_RESULT}/runner.exit || cat {GUEST_RESULT}/runner.exit; "
                          f"grep -c 'V1影子决策：' {GUEST_RESULT}/zenfs.run.log || true",20)
        print("本轮进度："+observation.strip(),flush=True)
    (output/"客机运行输出.log").write_text(transcript,encoding="utf-8")
    command(["scp", "-q", "-r", "-o", f"ControlPath={SOCKET}", "-o", "BatchMode=yes", "-P", "8089",
             f"femu@127.0.0.1:{GUEST_RESULT}",str(output)],120)
    command(["scp", "-q", "-o", f"ControlPath={SOCKET}", "-o", "BatchMode=yes", "-P", "8089",
             *[f"femu@127.0.0.1:{DIRECTORY}/{file}" for file in ("build.log","build.exit")],str(output)])
    require(child.exitstatus == 0,"业务或校验失败，日志已归档，未重跑")
    print("业务、只读校验和归档完成；未自动关机，等待独立核验",flush=True)


if __name__ == "__main__":
    main()
