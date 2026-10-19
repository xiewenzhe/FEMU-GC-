#!/usr/bin/env python3
"""补跑同一修复父镜像的900秒Greedy，随后自动生成三组逐文件成本对照。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
from types import SimpleNamespace

from run_stream_pair import require,PROJECT,IMAGE_DIR
from run_tail_ablation_fixed_pair import FixedPair,PARENT,PORT,DEVICE,running_parent,DIRECTORY as COMPILE_DIR

sys.path.insert(0,str(PROJECT/"research/rrzc"))
from analyze_fixed_three_way import GREEDY_LABEL,GREEDY_GUEST,analyze_greedy,analyze_all
from analyze_tail_ablation_fixed import LABEL,expected_software
from ablation_sampling import RULE

DIRECTORY = f"/home/femu/rrzc-{GREEDY_GUEST}-tests"
RESULT = f"/home/femu/rrzc-stream-results/{GREEDY_GUEST}"


def preflight():
    require(PARENT.is_file() and PARENT.resolve().parent==IMAGE_DIR and not running_parent(),"修复父镜像未停机或路径不符")
    require(Path("/dev/kvm").exists(),"KVM不可用")
    with socket.socket() as probe:
        require(probe.connect_ex(("127.0.0.1",int(PORT)))!=0,"8090已占用")
    for path in (IMAGE_DIR/f"{GREEDY_LABEL}.qcow2",Path(f"/tmp/{GREEDY_LABEL}.pid"),
                 Path(f"/tmp/{GREEDY_LABEL}.qmp"),Path(f"/tmp/{GREEDY_LABEL}-ssh.sock"),
                 PROJECT/"research/rrzc/results"/GREEDY_LABEL):
        require(not path.exists(),"新目标已存在，拒绝覆盖："+str(path))
    previous = PROJECT/"research/rrzc/results"/LABEL
    result = json.loads((previous/"完整对照核验.json").read_text())
    require(result["核验通过"],"原两组尚未核验通过")
    for arm in (result["保留尾部准入"],result["仅旁路尾部准入"]):
        require(arm["完整核心身份"]==expected_software(),"原两组非同一修复库")
    host = {n:d for d,n in (r.split(maxsplit=1) for r in (previous/"宿主软件身份.sha256").read_text().splitlines())}
    for path in (PROJECT/"build-femu/qemu-system-x86_64",PROJECT/"research/rrzc/tests/stream_rrzc_test.cc",
                 PROJECT/"research/rrzc/tests/run_guarded_stream_guest.sh",PROJECT/"research/rrzc/tests/cost_device_probe.cc"):
        digest = host.get(str(path),host.get(str(path.relative_to(PROJECT))))
        require(digest==hashlib.sha256(path.read_bytes()).hexdigest(),"原设备或业务源已改变："+str(path))
    for path,minimum in ((IMAGE_DIR,8<<30),(PROJECT,2<<30)):
        fs=os.statvfs(path);require(fs.f_bavail*fs.f_frsize>=minimum,"磁盘余量不足")


class GreedyRun(FixedPair):
    def archive_greedy(self, scp, query):
        errors=[]
        for remote in (DIRECTORY,COMPILE_DIR,RESULT):
            try:
                self.command(scp+["-r",f"femu@127.0.0.1:{remote}",str(self.output)],120)
            except Exception as error:
                errors.append(str(error)+"；"+remote)
        try:
            (self.output/"完整源码身份.after.sha256").write_text(self.ssh(query))
        except Exception as error:
            errors.append(str(error))
        (self.output/"归档状态.json").write_text(json.dumps(dict(完整=not errors,错误=errors),ensure_ascii=False,indent=2))
        return errors

    def run(self):
        self.state.pop("实验空间权重百分比")
        plan=dict(标签=GREEDY_LABEL,父镜像=str(PARENT),设备参数=DEVICE,业务秒数=900,预热秒数=180,
            实际策略="单Zone有效量Greedy；同样影子采样但不执行改选",RR阈值=4096,GC启动比例=20,
            回收资格MiB=192,采样规则=RULE,目标采样毫秒=500,覆盖界限秒数=2,核心身份=expected_software(),
            研究问题="与已有同版本keep／bypass逐文件比较，分开报告真正少复制、仅维护类型变化和新增复制",
            无自动重跑=True,统计范围="主要指标180至900秒；逐文件差额按全程闭合，终态空间另报",
            局限="补跑Greedy，单次三组对照，未随机化顺序；不据此宣称稳定显著收益")
        (self.output/"预注册计划.json").write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        snapshot=self.output/"本轮源码";snapshot.mkdir();(snapshot/"tests").mkdir()
        analysis=sorted((PROJECT/"research/rrzc").glob("*.py"))
        tests=sorted((PROJECT/"research/rrzc/tests").glob("*.py"))
        tests += [PROJECT/"research/rrzc/tests"/n for n in ("stream_rrzc_test.cc","cost_device_probe.cc",
                  "run_guarded_stream_guest.sh","run_fixed_greedy_guest.sh")]
        self.command(["cp",*map(str,analysis),str(snapshot)])
        self.command(["cp",*map(str,tests),str(snapshot/"tests")])
        (self.output/"宿主软件身份.sha256").write_text(self.command(["sha256sum","build-femu/qemu-system-x86_64",*map(str,analysis+tests)]))
        self.record(阶段="同版本Greedy和三组评价规则已登记",模式顺序=["greedy"],固定计划=plan)
        # 复用已验证启动方法，其固定标签加greedy恰为本轮全新目标。
        self.start("greedy")
        expected=expected_software();query="cd /home/femu/rocksdb; sha256sum "+" ".join(expected)
        identity=self.ssh(query)
        require({n:d for d,n in (r.split() for r in identity.splitlines())}==expected,"客机核心源／库不符")
        (self.output/"完整源码身份.before.sha256").write_text(identity)
        self.ssh(f"test ! -e {DIRECTORY} && test ! -e {COMPILE_DIR} && test ! -e {RESULT} && test ! -e {RESULT}-aux && mkdir {DIRECTORY} {COMPILE_DIR}")
        scp=["scp","-q","-o",f"ControlPath={self.socket}","-o","BatchMode=yes","-P",PORT]
        self.command(scp+["research/rrzc/tests/stream_rrzc_test.cc","research/rrzc/tests/cost_device_probe.cc",f"femu@127.0.0.1:{COMPILE_DIR}/"])
        self.command(scp+["research/rrzc/tests/stream_rrzc_test.cc","research/rrzc/tests/run_guarded_stream_guest.sh",
            "research/rrzc/tests/run_fixed_greedy_guest.sh","research/rrzc/tests/capture_fixed_failure.py",f"femu@127.0.0.1:{DIRECTORY}/"])
        try:
            self.record(阶段="同源同路径编译Greedy业务程序")
            # 使用与keep／bypass相同绝对编译路径，避免__FILE__造成二进制差异。
            for name in ("stream_rrzc_test","cost_device_probe"):
                self.ssh(f"cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti "
                    f"-DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude {COMPILE_DIR}/{name}.cc librocksdb_debug.a "
                    f"-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt -o {COMPILE_DIR}/{name} "
                    f"> {COMPILE_DIR}/{name}.build.log 2>&1 || status=$?; "
                    f"printf '%s\\n' \"$status\" > {COMPILE_DIR}/{name}.build.exit; exit \"$status\"",120)
            reference=json.loads((PROJECT/"research/rrzc/results"/(LABEL+"-keep")/"独立核验.json").read_text())
            actual=self.ssh(f"sha256sum {COMPILE_DIR}/stream_rrzc_test").split()[0]
            require(actual==reference["软件身份"]["stream_rrzc_test"],"业务二进制与原两组不同，停止而非放宽比较")
            self.ssh(f"cp {COMPILE_DIR}/stream_rrzc_test {COMPILE_DIR}/cost_device_probe {DIRECTORY}/ && cp {COMPILE_DIR}/stream_rrzc_test.build.exit {DIRECTORY}/build.exit")
            self.root(f"set -e; nohup setsid bash {DIRECTORY}/run_fixed_greedy_guest.sh < /dev/null "
                f"> {DIRECTORY}/launcher.log 2>&1 & printf '%s\\n' \"$!\" > {DIRECTORY}/launcher.pid")
            started=time.monotonic();complete=False
            while time.monotonic()-started<1260:
                progress=self.ssh(f"if test -f {DIRECTORY}/finished; then printf '包装已结束\\n'; fi; "
                    f"test ! -f {RESULT}/run.stdout.log || tail -1 {RESULT}/run.stdout.log; "
                    f"test ! -f {RESULT}/states.csv || tail -1 {RESULT}/states.csv; true")
                self.record(阶段="Greedy固定900秒业务／校验中",最近观测=progress)
                if "包装已结束" in progress:
                    complete=True;break
                time.sleep(15)
            require(complete,"固定观察超时，采证并保留业务")
            require(self.ssh(f"cat {DIRECTORY}/launcher.exit").strip()=="0","Greedy业务或包装失败")
        except Exception:
            try:
                (self.output/"失败线程现场.log").write_text(self.root(f"python3 {DIRECTORY}/capture_fixed_failure.py --strategy greedy",60))
            except Exception as error:
                (self.output/"采证错误.log").write_text(str(error))
            self.archive_greedy(scp,query)
            raise
        require(not self.archive_greedy(scp,query),"归档不完整，保留客机")
        (self.output/"launcher.exit").write_bytes((self.output/Path(DIRECTORY).name/"launcher.exit").read_bytes())
        self.record(阶段="Greedy归档完成，独立核验中")
        outcome=analyze_greedy(self.output/GREEDY_GUEST)
        (self.output/"独立核验.json").write_text(json.dumps(outcome,ensure_ascii=False,indent=2))
        self.record(阶段="Greedy数据和维护账目通过，正常关机")
        self.shutdown();self.password=None
        self.record(阶段="Greedy已完成关机，正在生成三组逐文件成本对照",当前镜像=None,当前客机PID=None,已完成=["greedy"])
        result=analyze_all()
        (self.output/"三组成本对照.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.record(阶段="Greedy和三组逐文件成本对照核验完成",已完成=["greedy","三组成本对照"])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check",action="store_true",help="只读前置检查")
    parser.add_argument("--detach",action="store_true",help="后台监督并自动核验归档")
    options=parser.parse_args();preflight()
    if options.check:
        print("同版本Greedy只读前置检查通过，未创建镜像或业务");return
    password=getpass.getpass("本轮Greedy认证（只驻留内存）：")
    output=PROJECT/"research/rrzc/results"/GREEDY_LABEL;output.mkdir()
    if options.detach:
        child=os.fork()
        if child:
            print(f"监督进程PID={child}，状态={output/'状态.json'}",flush=True);return
        os.setsid();log=os.open(output/"监督进程.log",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(log,1);os.dup2(log,2);null=os.open(os.devnull,os.O_RDONLY);os.dup2(null,0)
    run=GreedyRun(SimpleNamespace(label=GREEDY_LABEL,seconds=900,space_weight=50),password,output)
    try:
        run.run()
    except Exception as error:
        run.record(阶段="停止，需检查；未自动重跑或终止业务",错误=str(error))
        raise SystemExit(1)


if __name__=="__main__":main()
