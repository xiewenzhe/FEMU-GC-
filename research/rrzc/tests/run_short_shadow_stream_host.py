#!/usr/bin/env python3
"""仅本轮短期影子客机；复用原180秒滚动正负计划，实际仍Greedy；不管理原8080，不自动重跑。"""
import argparse
import getpass
import json
from pathlib import Path
import shlex
import subprocess
import time
import pexpect

PROJECT=Path(__file__).resolve().parents[3]


def require(ok, why):
    if not ok:
        raise RuntimeError(why)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile",choices=("original","maintenance-cases"))
    args=parser.parse_args()
    LABEL=f"short-shadow-stream-{args.profile}-20261014"
    GUEST_LABEL="v1-growth-stream-greedy-20261014" if args.profile=="original" else "short-shadow-cases-20261014"
    SOCKET=f"/tmp/{LABEL}-run-ssh.sock"
    DIRECTORY=f"/home/femu/rrzc-{GUEST_LABEL}-tests"
    GUEST_RESULT=f"/home/femu/rrzc-stream-results/{GUEST_LABEL}"
    output=PROJECT/"research/rrzc/results"/LABEL
    require(not output.exists() and not Path(SOCKET).exists(),"本轮结果或连接已存在，拒绝覆盖")
    pid=int(Path(f"/tmp/{LABEL}.pid").read_text())
    cmd=Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    require(f"file=/home/data/xwz/rrzc-baselines/{LABEL}.qcow2,if=none,aio=native,cache=none,format=qcow2,id=hd0".encode() in cmd,
            "PID不对应本轮新子镜像")
    output.mkdir()
    password=getpass.getpass("本轮隔离客机密码（仅驻留内存）：")
    child=pexpect.spawn("ssh",["-o","ControlMaster=yes","-o",f"ControlPath={SOCKET}",
        "-o","ControlPersist=900","-o","PreferredAuthentications=password","-o","PubkeyAuthentication=no",
        "-o","StrictHostKeyChecking=yes","-o","ConnectTimeout=8","-p","8089","femu@127.0.0.1","id"],
        encoding="utf-8",timeout=20)
    child.expect(r"[Pp]assword:"); child.sendline(password)
    child.expect(pexpect.EOF,timeout=20); login=child.before; child.close()
    require(child.exitstatus==0 and "uid=1000(femu)" in login,"认证失败，不重试密码")

    def command(args, timeout=60, check=True):
        result=subprocess.run(args,cwd=PROJECT,text=True,stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,timeout=timeout)
        with (output/"管理命令.log").open("a",encoding="utf-8") as stream:
            stream.write(json.dumps(args,ensure_ascii=False)+"\n"+result.stdout+"\n")
        if check:
            require(result.returncode==0,"命令失败，保留本轮记录")
        return result

    def ssh(text, timeout=60, check=True):
        return command(["ssh","-S",SOCKET,"-o","BatchMode=yes","-p","8089","femu@127.0.0.1",text],timeout,check)

    print("认证通过，核对已编译短期影子源码及静态库",flush=True)
    identity=ssh("cd /home/femu/rocksdb; sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h "
        "plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc "
        "plugin/zenfs/fs/zbdlib_zenfs.h plugin/zenfs/fs/rrzc_recent_log.h plugin/zenfs/fs/rrzc_urgency_selector.h "
        "plugin/zenfs/fs/rrzc_v1_execution.h plugin/zenfs/fs/rrzc_v1_growth.h "
        "plugin/zenfs/fs/rrzc_short_shadow.h librocksdb_debug.a").stdout
    previous=PROJECT/"research/rrzc/results/short-shadow-build-20261014/rrzc-short-shadow-build-20261014"
    expected={"plugin/zenfs/"+name:digest for digest,name in
              (line.split() for line in (previous/"source.after.sha256").read_text().splitlines())}
    expected.update({name:digest for digest,name in (line.split() for line in (previous/"binaries.sha256").read_text().splitlines())
                     if name=="librocksdb_debug.a"})
    actual={name:digest for digest,name in (line.split() for line in identity.splitlines())}
    require(all(actual.get(name)==digest for name,digest in expected.items()),"客机源/库不匹配短期影子编译基底")
    (output/"客机编译前身份.sha256").write_text(identity,encoding="utf-8")
    ssh(f"test ! -e {DIRECTORY} && mkdir {DIRECTORY}")
    common=["scp","-q","-o",f"ControlPath={SOCKET}","-o","BatchMode=yes","-P","8089"]
    guest_script="run_v1_growth_stream_guest.sh" if args.profile=="original" else "run_short_shadow_cases_guest.sh"
    command(common+["research/rrzc/tests/stream_rrzc_test.cc","research/rrzc/tests/"+guest_script,
                    "research/rrzc/tests/run_short_shadow_stream_guest.sh",
                    f"femu@127.0.0.1:{DIRECTORY}/"])
    result=ssh(f"cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX "
        f"-I. -Iinclude {DIRECTORY}/stream_rrzc_test.cc librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt "
        f"-o {DIRECTORY}/stream_rrzc_test > {DIRECTORY}/build.log 2>&1 || status=$?; "
        f"printf '%s\\n' \"$status\" > {DIRECTORY}/build.exit; cat {DIRECTORY}/build.log; exit \"$status\"",120,False)
    command(common+[f"femu@127.0.0.1:{DIRECTORY}/build.log",f"femu@127.0.0.1:{DIRECTORY}/build.exit",str(output)])
    require(result.returncode==0,"测试程序编译失败，保留日志，不启动设备实验")
    print("编译通过，开始180秒滚动计划/"+args.profile+"及独立只读校验；不是稳态长测",flush=True)
    root="sudo -S -- bash -c "+shlex.quote(f"printf 'V1认证完成\\n'; bash {DIRECTORY}/run_short_shadow_stream_guest.sh {args.profile}")
    child=pexpect.spawn("ssh",["-S",SOCKET,"-o","BatchMode=yes","-p","8089","-tt","femu@127.0.0.1",root],encoding="utf-8",timeout=20)
    status=child.expect([r"\[sudo\] password for femu:","V1认证完成",pexpect.EOF])
    if status==0:
        child.sendline(password); child.expect("V1认证完成",timeout=20)
    else:
        require(status==1,"sudo认证失败，不继续设备操作")
    password=None; started=time.monotonic()
    while child.expect([pexpect.EOF,pexpect.TIMEOUT],timeout=30)==1:
        require(time.monotonic()-started<540,"超过受控短测时限，保留客机，不自动杀进程或重跑")
        observation=ssh(f"tail -1 {GUEST_RESULT}/run.stdout.log; test ! -e {GUEST_RESULT}/runner.exit || cat {GUEST_RESULT}/runner.exit",20,False)
        print("本轮进度："+observation.stdout.strip(),flush=True)
    transcript=child.before; child.close()
    (output/"客机业务输出.log").write_text(transcript,encoding="utf-8")
    command(common+["-r",f"femu@127.0.0.1:{GUEST_RESULT}",str(output)],120)
    require(child.exitstatus==0,"固定业务或只读校验失败，日志已归档，未重跑")
    print("固定业务、只读校验及归档完成；等待独立日志核验，未自动关机",flush=True)


if __name__=="__main__":
    main()
