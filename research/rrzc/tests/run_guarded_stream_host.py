#!/usr/bin/env python3
"""仅全新8089子镜像；默认180秒，显式中测900秒，不管理8080或自动重跑。"""
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
    if not ok: raise RuntimeError(why)


def main(argv=None,password=None,reuse_control=False):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("strategy",choices=("greedy","guarded"))
    parser.add_argument("--profile",choices=("original","maintenance-cases"),default="original")
    parser.add_argument("--medium",action="store_true",help="独立固定900秒原滚动配对，旧默认不变")
    parser.add_argument("--tail-ablation",choices=("keep","bypass"),help="仅新900秒同库尾部消融，旧入口不变")
    options=parser.parse_args(argv);strategy=options.strategy;profile=options.profile
    require(not options.medium or profile=="original","中测仅允许原滚动请求计划")
    require(not options.tail_ablation or options.medium and strategy=="guarded","消融仅允许900秒真实执行")
    family="stream" if profile=="original" else "cases"
    LABEL=f"guarded-medium-20261015-{strategy}" if options.medium else f"guarded-{family}-{strategy}-20261014"
    guest_label=LABEL
    if options.tail_ablation:LABEL=f"tail-ablation-medium-20261015-{options.tail_ablation}"
    seconds=900 if options.medium else 180
    SOCKET=f"/tmp/{LABEL}-ssh.sock"
    DIRECTORY=f"/home/femu/rrzc-{guest_label}-tests"
    RESULT=f"/home/femu/rrzc-stream-results/{guest_label}"
    output=PROJECT/"research/rrzc/results"/LABEL
    require(not output.exists() and (not Path(SOCKET).exists() or reuse_control and options.medium),"本轮结果或连接已存在，拒绝覆盖")
    pid=int(Path(f"/tmp/{LABEL}.pid").read_text())
    args=Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    require(f"file=/home/data/xwz/rrzc-baselines/{LABEL}.qcow2,if=none,aio=native,cache=none,format=qcow2,id=hd0".encode() in args,
            "PID不是本轮新子镜像")
    output.mkdir()
    if password is None:password=getpass.getpass("本轮隔离客机密码（仅驻留内存）：")
    if not reuse_control:
        login=pexpect.spawn("ssh",["-o","ControlMaster=yes","-o",f"ControlPath={SOCKET}","-o","ControlPersist=1800" if options.medium else "ControlPersist=900",
        "-o","PreferredAuthentications=password","-o","PubkeyAuthentication=no","-o","StrictHostKeyChecking=yes",
        "-o","ConnectTimeout=8","-p","8089","femu@127.0.0.1","id"],encoding="utf-8",timeout=20)
        login.expect(r"[Pp]assword:"); login.sendline(password); login.expect(pexpect.EOF); text=login.before; login.close()
        require(login.exitstatus==0 and "uid=1000(femu)" in text,"认证失败，不重试密码")

    def command(cmd, timeout=60, check=True):
        r=subprocess.run(cmd,cwd=PROJECT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        with (output/"管理命令.log").open("a") as f: f.write(json.dumps(cmd,ensure_ascii=False)+"\n"+r.stdout+"\n")
        if check: require(r.returncode==0,"命令失败，保留记录，不重跑业务")
        return r

    def ssh(cmd, timeout=60, check=True):
        return command(["ssh","-S",SOCKET,"-o","BatchMode=yes","-p","8089","femu@127.0.0.1",cmd],timeout,check)

    require("uid=1000(femu)" in ssh("id").stdout,"专用控制连接身份未确认")

    build=PROJECT/"research/rrzc/results/guarded-build-20261014/rrzc-guarded-build-20261014"
    if options.tail_ablation:
        build=PROJECT/"research/rrzc/results/tail-ablation-build-20261015/rrzc-tail-ablation-build-20261015"
    expected={"plugin/zenfs/"+name:digest for digest,name in (line.split() for line in (build/"source.after.sha256").read_text().splitlines())}
    expected["librocksdb_debug.a"]=next(digest for digest,name in
        (line.split() for line in (build/"binaries.sha256").read_text().splitlines()) if name=="librocksdb_debug.a")
    identity=ssh("cd /home/femu/rocksdb; sha256sum "+" ".join(expected)).stdout
    actual={name:digest for digest,name in (line.split() for line in identity.splitlines())}
    require(actual==expected,"源码/静态库不是已核验独立约束基底")
    (output/"客机编译前身份.sha256").write_text(identity)
    if options.tail_ablation:(output/"完整源码身份.before.sha256").write_text(identity)
    ssh(f"test ! -e {DIRECTORY} && test ! -e {RESULT} && mkdir {DIRECTORY}")
    scp=["scp","-q","-o",f"ControlPath={SOCKET}","-o","BatchMode=yes","-P","8089"]
    command(scp+["research/rrzc/tests/stream_rrzc_test.cc","research/rrzc/tests/cost_device_probe.cc",
                 "research/rrzc/tests/run_guarded_stream_guest.sh",f"femu@127.0.0.1:{DIRECTORY}/"])
    for name in ("stream_rrzc_test","cost_device_probe"):
        r=ssh(f"cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti -DOS_LINUX -DROCKSDB_PLATFORM_POSIX "
            f"-I. -Iinclude {DIRECTORY}/{name}.cc librocksdb_debug.a -lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt "
            f"-o {DIRECTORY}/{name} > {DIRECTORY}/{name}.build.log 2>&1 || status=$?; "
            f"printf '%s\\n' \"$status\" > {DIRECTORY}/{name}.build.exit; exit \"$status\"",120,False)
        command(scp+[f"femu@127.0.0.1:{DIRECTORY}/{name}.build.log",f"femu@127.0.0.1:{DIRECTORY}/{name}.build.exit",str(output)])
        require(r.returncode==0,"测试编译失败，未启动设备实验")
    ssh(f"cp {DIRECTORY}/stream_rrzc_test.build.exit {DIRECTORY}/build.exit")
    print(f"编译和身份核验通过，固定{seconds}秒独立约束配对；不事后调负载或延长窗口",flush=True)
    root="sudo -S -- bash -c "+shlex.quote(f"set -e; printf '尾部认证完成\\n'; "
        f"printf 'mq-deadline\\n' > /sys/block/nvme0n1/queue/scheduler; "
        f"env -u ZENFS_MIGRATE_RESERVE {DIRECTORY}/cost_device_probe {DIRECTORY}/probe-device.log > {DIRECTORY}/probe.csv; "+
        (f"env ZENFS_RRZC_TAIL_ABLATION={options.tail_ablation} " if options.tail_ablation else "")+
        f"bash {DIRECTORY}/run_guarded_stream_guest.sh {strategy} {profile}"+(" medium" if options.medium else ""))
    child=pexpect.spawn("ssh",["-S",SOCKET,"-o","BatchMode=yes","-p","8089","-tt","femu@127.0.0.1",root],encoding="utf-8",timeout=20)
    status=child.expect([r"\[sudo\] password for femu:","尾部认证完成",pexpect.EOF])
    if status==0: child.sendline(password); child.expect("尾部认证完成")
    else: require(status==1,"sudo认证失败，不继续")
    password=None; started=time.monotonic()
    while child.expect([pexpect.EOF,pexpect.TIMEOUT],timeout=30)==1:
        require(time.monotonic()-started<seconds+360,"超过固定观察时限，保留客机和日志，不自动杀进程")
        r=ssh(f"tail -1 {RESULT}/run.stdout.log",20,False)
        print("本轮进度："+r.stdout.strip(),flush=True)
    transcript=child.before; child.close(); (output/"客机业务输出.log").write_text(transcript)
    command(scp+["-r",f"femu@127.0.0.1:{DIRECTORY}",str(output)],120)
    if ssh(f"test -d {RESULT}",check=False).returncode==0:
        command(scp+["-r",f"femu@127.0.0.1:{RESULT}",str(output)],120)
    if options.tail_ablation:
        after=ssh("cd /home/femu/rocksdb; sha256sum "+" ".join(expected)).stdout
        (output/"完整源码身份.after.sha256").write_text(after)
        require(after==identity,"运行期间完整源码或库发生变化")
    require(child.exitstatus==0,"功能观察或只读数据校验失败，原错误已归档，未重跑")
    print("固定业务、独立数据校验及完整归档完成；等待日志核验，未自动关机",flush=True)


if __name__=="__main__": main()
