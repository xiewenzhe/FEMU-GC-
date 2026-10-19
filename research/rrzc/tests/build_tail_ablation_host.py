#!/usr/bin/env python3
"""仅新子镜像构建消融入口；密码仅在内存，不初始化设备或启动收益实验。"""
import getpass
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from run_stream_pair import Pair,require,PROJECT,IMAGE_DIR

LABEL="tail-ablation-build-20261015"
DIRECTORY="/home/femu/rrzc-"+LABEL
PARENT=IMAGE_DIR/"guarded-build-20261014.qcow2"


def main():
    output=PROJECT/"research/rrzc/results"/LABEL
    require(not output.exists(),"构建结果已存在，拒绝覆盖")
    require(PARENT.is_file(),"已核验父镜像不存在")
    password=getpass.getpass("新构建客机认证（仅驻留内存）：")
    output.mkdir()
    pair=Pair(SimpleNamespace(label=LABEL,seconds=0,space_weight=50),password,output)
    pair.state.pop("实验空间权重百分比")
    pair.state["模式顺序"]=["仅构建，不运行实验"]
    try:
        pair.start_vm("build",PARENT)
        pair.record(阶段="核对构建前身份")
        previous=PROJECT/"research/rrzc/results/guarded-build-20261014/rrzc-guarded-build-20261014"
        expected={"plugin/zenfs/"+name:digest for digest,name in
                  (row.split() for row in (previous/"source.after.sha256").read_text().splitlines())}
        expected["librocksdb_debug.a"]=next(digest for digest,name in
            (row.split() for row in (previous/"binaries.sha256").read_text().splitlines()) if name=="librocksdb_debug.a")
        text=pair.ssh("cd /home/femu/rocksdb; sha256sum "+" ".join(expected))
        actual={name:digest for digest,name in (row.split() for row in text.splitlines())}
        require(actual==expected,"父镜像源或库身份不匹配，停止")
        (output/"客机编译前身份.sha256").write_text(text)
        pair.ssh(f"test ! -e {DIRECTORY} && mkdir -p {DIRECTORY}/model {DIRECTORY}/tests")
        scp=["scp","-q","-o",f"ControlPath={pair.socket}","-o","BatchMode=yes","-P","8089"]
        pair.command(scp+["research/rrzc/zenfs-tail-ablation.patch","research/rrzc/tests/build_tail_ablation_guest.sh",
            str(previous/"source.after.sha256"),f"femu@127.0.0.1:{DIRECTORY}/"])
        pair.ssh(f"mv {DIRECTORY}/source.after.sha256 {DIRECTORY}/source.guarded.sha256")
        pair.command(scp+["research/rrzc/model/rrzc_tail_ablation.h",f"femu@127.0.0.1:{DIRECTORY}/model/"])
        pair.command(scp+["research/rrzc/tests/test_rrzc_tail_ablation.cc",f"femu@127.0.0.1:{DIRECTORY}/tests/"])
        pair.record(阶段="完整库构建与消融合成测试")
        code=0
        try:
            pair.ssh(f"bash {DIRECTORY}/build_tail_ablation_guest.sh",timeout=600)
        except RuntimeError:
            code=1
        # 构建错误也归档；不自动应用第二次补丁或重跑构建。
        pair.command(scp+["-r",f"femu@127.0.0.1:{DIRECTORY}",str(output)],timeout=120)
        require(code==0,"构建失败，原错误已归档，客机留供检查")
        archive=output/Path(DIRECTORY).name
        for name in ("library-build.exit","test-build.exit","test-run.exit"):
            require((archive/name).read_text().strip()=="0","构建或测试没有成功")
        before={name:digest for digest,name in (row.split() for row in (archive/"source.before.sha256").read_text().splitlines())}
        after={name:digest for digest,name in (row.split() for row in (archive/"source.after.sha256").read_text().splitlines())}
        require(set(after)==set(before)|{"fs/rrzc_tail_ablation.h"},"源文件名单发生额外变化")
        require(all(after[name]==digest for name,digest in before.items() if name!="fs/fs_zenfs.cc"),"旧核心文件发生额外修改")
        for name,local in (("fs/fs_zenfs.cc",PROJECT/"build-femu/rrzc/tail-ablation-work/fs/fs_zenfs.cc"),
                           ("fs/rrzc_tail_ablation.h",PROJECT/"research/rrzc/model/rrzc_tail_ablation.h")):
            digest=pair.command(["sha256sum",str(local)]).split()[0]
            require(after[name]==digest,"客机与本轮新源码不一致")
        (output/"构建核验.json").write_text(json.dumps(dict(核验通过=True,旧文件保持不变=True,
            修改文件=["fs/fs_zenfs.cc","fs/rrzc_tail_ablation.h"],设备业务运行=False,
            已核验净收益=False),ensure_ascii=False,indent=2))
        pair.record(阶段="构建核验通过，正常关机")
        pair.privileged("poweroff",60,"build")
        for _ in range(30):
            if not Path(f"/proc/{pair.state['当前客机PID']}").exists():break
            time.sleep(1)
        require(not Path(f"/proc/{pair.state['当前客机PID']}").exists(),"构建客机未完全关闭")
        pair.password=None
        pair.record(阶段="完整构建与合成测试通过，客机已关机，未启动实验",当前镜像=None,当前客机PID=None,已完成=["构建"])
    except Exception as error:
        pair.record(阶段="停止，需检查",错误=str(error))
        raise


if __name__=="__main__":main()
