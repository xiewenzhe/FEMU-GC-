#!/usr/bin/env python3
"""只用已核验8090新客机；300秒功能回归脱离SSH伪终端，失败不杀业务。"""
import getpass
import json
from pathlib import Path
import shlex
import sys
import time
from types import SimpleNamespace
import pexpect

from run_stream_pair import require, PROJECT, IMAGE_DIR
from build_reopen_lock_host import BuildPair, PORT

LABEL="reopen-lock-short-20261015"
BUILD="reopen-lock-build-20261015"
DIRECTORY=f"/home/femu/rrzc-{LABEL}-tests"
RESULT=f"/home/femu/rrzc-stream-results/{LABEL}"


def main():
    output=PROJECT/"research/rrzc/results"/LABEL
    require(not output.exists(),"本轮短测结果已存在，拒绝覆盖")
    state=json.loads((PROJECT/"research/rrzc/results"/BUILD/"状态.json").read_text())
    require(state["已完成"]==["构建和内存回归"],"新库尚未通过构建核验")
    pid=state["当前客机PID"]
    args=Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    require(f"file={IMAGE_DIR}/{BUILD}.qcow2,if=none,aio=native,cache=none,format=qcow2,id=hd0".encode() in args,
            "目标PID不是新8090子镜像")
    password=getpass.getpass("8090短测启动认证（只驻留内存）：")
    output.mkdir()
    pair=BuildPair(SimpleNamespace(label=LABEL,seconds=300,space_weight=50),None,output)
    pair.socket=f"/tmp/{BUILD}-ssh.sock"
    pair.state.pop("实验空间权重百分比")
    pair.state.update(模式顺序=["修复后bypass功能回归"],当前客机PID=pid,当前镜像=state["当前镜像"])
    try:
        require("uid=1000(femu)" in pair.ssh("id"),"8090控制连接不符")
        archive=PROJECT/"research/rrzc/results"/BUILD/f"rrzc-{BUILD}"
        expected={"plugin/zenfs/"+n:d for d,n in (r.split() for r in (archive/"source.after.sha256").read_text().splitlines())}
        expected["librocksdb_debug.a"]=next(d for d,n in
            (r.split() for r in (archive/"binaries.sha256").read_text().splitlines()) if n=="librocksdb_debug.a")
        identity=pair.ssh("cd /home/femu/rocksdb; sha256sum "+" ".join(expected))
        require({n:d for d,n in (r.split() for r in identity.splitlines())}==expected,"实际源／库不符")
        (output/"源码身份.before.sha256").write_text(identity)
        plan=dict(固定业务秒数=300,目的="越过原240秒停点的功能回归，不比较收益",尾部模式="bypass",
            RR阈值=4096,GC启动比例=20,紧迫秒数=10,额外预算MiB=16,回收资格MiB=192,
            原业务源SHA256="277ce66cf22a0a665eec2cf23d504e83058165971df6d5c5c3911b74868d276e",
            无自动重跑=True,新保护="远端nohup＋setsid且stdin脱离PTY；宿主超时不发送终止信号")
        (output/"固定计划.json").write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        pair.ssh(f"test ! -e {DIRECTORY} && test ! -e {RESULT} && mkdir {DIRECTORY}")
        scp=["scp","-q","-o",f"ControlPath={pair.socket}","-o","BatchMode=yes","-P",PORT]
        pair.command(scp+["research/rrzc/tests/stream_rrzc_test.cc","research/rrzc/tests/run_reopen_lock_guest.sh",
            f"femu@127.0.0.1:{DIRECTORY}/"])
        pair.record(阶段="8090短测程序编译")
        success=True
        try:
            pair.ssh(f"cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti "
                f"-DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude {DIRECTORY}/stream_rrzc_test.cc librocksdb_debug.a "
                f"-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt -o {DIRECTORY}/stream_rrzc_test "
                f"> {DIRECTORY}/build.log 2>&1 || status=$?; printf '%s\\n' \"$status\" > {DIRECTORY}/build.exit; exit \"$status\"",120)
        except RuntimeError:
            success=False
        pair.command(scp+[f"femu@127.0.0.1:{DIRECTORY}/build.log",f"femu@127.0.0.1:{DIRECTORY}/build.exit",str(output)])
        require(success,"测试编译失败，不初始化设备")
        root="sudo -S -- bash -c "+shlex.quote(f"set -e; printf '短测认证完成\\n'; "
            f"nohup setsid bash {DIRECTORY}/run_reopen_lock_guest.sh < /dev/null > {DIRECTORY}/launcher.log 2>&1 & "
            f"printf '%s\\n' \"$!\" > {DIRECTORY}/launcher.pid")
        child=pexpect.spawn("ssh",["-S",pair.socket,"-o","BatchMode=yes","-p",PORT,"-tt","femu@127.0.0.1",root],encoding="utf-8",timeout=20)
        reply=child.expect([r"\[sudo\] password for femu:","短测认证完成",pexpect.EOF])
        if reply==0:
            child.sendline(password);child.expect("短测认证完成")
        else:require(reply==1,"sudo未成功，不启动业务")
        password=None
        child.expect(pexpect.EOF);child.close()
        require(child.exitstatus==0,"远端独立启动失败")
        pair.record(阶段="固定300秒业务运行，已脱离SSH伪终端",固定计划=plan)
        started=time.monotonic();complete=False
        while time.monotonic()-started<450:
            progress=pair.ssh(f"if test -f {RESULT}/logs.sha256; then printf '归档可取回\\n'; fi; "
                f"test ! -f {RESULT}/run.stdout.log || tail -1 {RESULT}/run.stdout.log; "
                f"test ! -f {RESULT}/states.csv || tail -1 {RESULT}/states.csv")
            pair.record(阶段="固定300秒业务／校验中",最近观测=progress)
            if "归档可取回" in progress:
                complete=True;break
            time.sleep(15)
        # 超时也取回当前证据，绝不关闭伪终端去终止远端业务。
        pair.command(scp+["-r",f"femu@127.0.0.1:{DIRECTORY}",str(output)],120)
        pair.command(scp+["-r",f"femu@127.0.0.1:{RESULT}",str(output)],120)
        after=pair.ssh("cd /home/femu/rocksdb; sha256sum "+" ".join(expected))
        (output/"源码身份.after.sha256").write_text(after)
        require(complete,"固定观察超时，已归档，远端进程保留，未杀进程或重跑")
        run=output/LABEL
        for name in ("mkfs.exit","run.exit","verify.exit","runner.exit"):
            require((run/name).read_text().strip()=="0","业务／数据校验未成功")
        require(identity==after,"业务期间核心源或库改变")
        pair.record(阶段="300秒业务与只读数据校验通过，原始记录已归档，待独立核验",已完成=["固定业务和数据校验"])
    except Exception as error:
        pair.record(阶段="停止，需检查；未终止远端业务或重跑",错误=str(error))
        raise


if __name__=="__main__":main()
