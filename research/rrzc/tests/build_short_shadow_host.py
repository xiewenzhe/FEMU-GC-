#!/usr/bin/env python3
"""仅管理本轮8089新子镜像的编译；不初始化块设备，不运行性能实验。"""
import getpass
from pathlib import Path
import subprocess
import pexpect

PROJECT=Path(__file__).resolve().parents[3]
LABEL="short-shadow-build-20261014"
SOCKET=f"/tmp/{LABEL}-ssh.sock"
DIRECTORY=f"/home/femu/rrzc-{LABEL}"


def require(ok, why):
    if not ok:
        raise RuntimeError(why)


def main():
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
    password=None
    child.expect(pexpect.EOF,timeout=20); login=child.before; child.close()
    require(child.exitstatus==0 and "uid=1000(femu)" in login,"认证失败，不重试密码")

    def command(args, timeout=60, check=True):
        result=subprocess.run(args,cwd=PROJECT,text=True,stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,timeout=timeout)
        with (output/"管理命令.log").open("a",encoding="utf-8") as stream:
            stream.write(result.stdout+"\n")
        if check:
            require(result.returncode==0,"命令失败，保留本轮记录")
        return result

    def ssh(text, timeout=60, check=True):
        return command(["ssh","-S",SOCKET,"-o","BatchMode=yes","-p","8089","femu@127.0.0.1",text],timeout,check)

    print("认证通过，核对继承源码及库；不访问实验NVMe",flush=True)
    identity=ssh("cd /home/femu/rocksdb; sha256sum plugin/zenfs/fs/fs_zenfs.cc plugin/zenfs/fs/fs_zenfs.h "
        "plugin/zenfs/fs/zbd_zenfs.cc plugin/zenfs/fs/zbd_zenfs.h plugin/zenfs/fs/zbdlib_zenfs.cc "
        "plugin/zenfs/fs/zbdlib_zenfs.h plugin/zenfs/fs/rrzc_recent_log.h plugin/zenfs/fs/rrzc_urgency_selector.h "
        "plugin/zenfs/fs/rrzc_v1_execution.h plugin/zenfs/fs/rrzc_v1_growth.h librocksdb_debug.a").stdout
    previous=PROJECT/"research/rrzc/results/v1-growth2-build-20261014/rrzc-v1-growth2-build-20261014"
    expected={"plugin/zenfs/"+name:digest for digest,name in (line.split() for line in (previous/"source.after.sha256").read_text().splitlines())}
    expected.update({name:digest for digest,name in (line.split() for line in (previous/"binaries.sha256").read_text().splitlines())
                     if name=="librocksdb_debug.a"})
    actual={name:digest for digest,name in (line.split() for line in identity.splitlines())}
    require(all(actual.get(name)==digest for name,digest in expected.items()),"客机源/库不匹配已验证真实入口基底")
    (output/"客机编译前身份.sha256").write_text(identity,encoding="utf-8")
    ssh(f"test ! -e {DIRECTORY} && mkdir -p {DIRECTORY}/model {DIRECTORY}/tests")
    common=["scp","-q","-o",f"ControlPath={SOCKET}","-o","BatchMode=yes","-P","8089"]
    command(common+["research/rrzc/zenfs-short-shadow.patch","research/rrzc/tests/build_short_shadow_guest.sh",
                    str(previous/"source.after.sha256"),f"femu@127.0.0.1:{DIRECTORY}/"])
    ssh(f"mv {DIRECTORY}/source.after.sha256 {DIRECTORY}/source.growth.sha256")
    names=("rrzc_recent_log.h","rrzc_weighted_selector.h","rrzc_urgency_selector.h","rrzc_v1_growth.h","rrzc_v1_execution.h","rrzc_short_shadow.h")
    command(common+[str(PROJECT/"research/rrzc/model"/name) for name in names]+[f"femu@127.0.0.1:{DIRECTORY}/model/"])
    command(common+["research/rrzc/tests/test_rrzc_short_shadow.cc",f"femu@127.0.0.1:{DIRECTORY}/tests/"])
    print("基底匹配，开始客机完整ZenFS/RocksDB构建与短期影子合成测试",flush=True)
    result=ssh(f"bash {DIRECTORY}/build_short_shadow_guest.sh",600,False)
    (output/"客机编译输出.log").write_text(result.stdout,encoding="utf-8")
    # 失败也归档整个本轮目录，不能用重跑覆盖原始错误。
    command(common+["-r",f"femu@127.0.0.1:{DIRECTORY}",str(output)],120)
    require(result.returncode==0,"客机构建失败，日志已取回，未自动重跑")
    archive=output/Path(DIRECTORY).name
    after={name:digest for digest,name in (line.split() for line in (archive/"source.after.sha256").read_text().splitlines())}
    for name,digest in after.items():
        expected_hash=command(["sha256sum",str(PROJECT/"build-femu/rrzc/short-shadow-work"/name)]).stdout.split()[0]
        require(expected_hash==digest,f"客机与本轮源镜像不同：{name}")
    print("完整构建、短期影子合成测试及源码一致性核验通过；未运行SSD改选或收益实验",flush=True)


if __name__=="__main__":
    main()
