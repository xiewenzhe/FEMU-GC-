#!/usr/bin/env python3
"""独立180秒三候选竞争短测；不改生产模型，不覆盖旧负载或镜像。"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace

from run_tail_ablation_fixed_pair import FixedPair, PARENT, PORT, running_parent, expected_software
from run_stream_pair import PROJECT, IMAGE_DIR, require
from analyze_competition_workload import analyze

PREFIX="model-competition-20261015"
LABEL=PREFIX+"-greedy"
DIRECTORY=f"/home/femu/rrzc-{LABEL}-tests"
RESULT=f"/home/femu/rrzc-stream-results/{LABEL}"


def preflight():
    require(PARENT.is_file() and not running_parent(),"父镜像缺失或仍运行")
    require(Path('/dev/kvm').exists(),"KVM不可用")
    with socket.socket() as probe:
        require(probe.connect_ex(('127.0.0.1',int(PORT)))!=0,"8090被占用")
    for p in (IMAGE_DIR/f'{LABEL}.qcow2',Path(f'/tmp/{LABEL}.pid'),Path(f'/tmp/{LABEL}.qmp'),
              Path(f'/tmp/{LABEL}-ssh.sock'),PROJECT/'research/rrzc/results'/LABEL):
        require(not p.exists(),"目标已存在，拒绝覆盖："+str(p))
    for p,minimum in ((IMAGE_DIR,8<<30),(PROJECT,2<<30)):
        s=os.statvfs(p);require(s.f_bavail*s.f_frsize>=minimum,"空间不足")
    require(hashlib.sha256((PROJECT/'research/rrzc/tests/stream_rrzc_test.cc').read_bytes()).hexdigest()==
            '277ce66cf22a0a665eec2cf23d504e83058165971df6d5c5c3911b74868d276e',"原负载已改变")
    expected_software()


class ModelRun(FixedPair):
    label_prefix=PREFIX

    def run(self):
        self.state.pop('实验空间权重百分比')
        plan=dict(固定业务秒数=180,策略='Greedy，模型只做影子观测',每代锚点MiB=[16,24,32],
                  固定填充区数=18,每代写入MiB=768,代际秒数=30,持续热读起始年龄秒=58,
                  持续热读时长秒=24,热文件每秒读取次数=[28,32],读计划='两种热成本同期竞争，冷区读分散八块',RR阈值=4096,GC启动比例=20,
                  额外预算MiB=16,紧迫秒数=10,目标='核验至少两个准入紧迫候选及成本／ETA排序冲突，不要求正收益',生产模型修改=False,
                  失败处理='归档已有证据，保留业务和客机，不自动重跑',父镜像=str(PARENT))
        (self.output/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
        self.record(阶段='计划已固定',模式顺序=['greedy'],固定计划=plan)
        sources=['competition_workload_test.cc','competition_workload_plan.h','run_competition_workload_guest.sh']
        self.command(['cp',*[str(PROJECT/'research/rrzc/tests'/n) for n in sources],str(self.output)])
        self.command(['cp',str(Path(__file__).resolve()),str(PROJECT/'research/rrzc/analyze_competition_workload.py'),str(self.output)])
        (self.output/'宿主FEMU.sha256').write_text(self.command(['sha256sum','build-femu/qemu-system-x86_64']))
        self.start('greedy')
        expected=expected_software()
        query='cd /home/femu/rocksdb; sha256sum '+' '.join(expected)
        identity=self.ssh(query)
        require({n:d for d,n in (s.split() for s in identity.splitlines())}==expected,'核心源／库不符')
        (self.output/'源码身份.before.sha256').write_text(identity)
        scp=['scp','-q','-o',f'ControlPath={self.socket}','-o','BatchMode=yes','-P',PORT]
        try:
            self.ssh(f'test ! -e {DIRECTORY} && test ! -e {RESULT} && test ! -e {RESULT}-aux && mkdir {DIRECTORY}')
            self.command(scp+[str(PROJECT/'research/rrzc/tests'/n) for n in sources]+[f'femu@127.0.0.1:{DIRECTORY}/'])
            self.record(阶段='编译独立竞争负载，固定生产库不变')
            self.ssh(f'cd /home/femu/rocksdb; status=0; g++ -std=c++20 -Wall -Wextra -Werror -fno-rtti '
                f'-DOS_LINUX -DROCKSDB_PLATFORM_POSIX -I. -Iinclude {DIRECTORY}/competition_workload_test.cc librocksdb_debug.a '
                f'-lzbd -lsnappy -llz4 -lzstd -lz -lbz2 -pthread -ldl -lrt -o {DIRECTORY}/competition_workload_test '
                f'> {DIRECTORY}/build.log 2>&1 || status=$?; printf "%s\\n" "$status" > {DIRECTORY}/build.exit; exit "$status"',120)
            self.root(f'set -e; nohup setsid bash {DIRECTORY}/run_competition_workload_guest.sh < /dev/null '
                      f'> {DIRECTORY}/launcher.log 2>&1 & printf "%s\\n" "$!" > {DIRECTORY}/launcher.pid')
            started=time.monotonic();complete=False
            while time.monotonic()-started<420:
                progress=self.ssh(f'if test -f {DIRECTORY}/finished; then printf "包装已结束\\n"; fi; '
                    f'test ! -f {RESULT}/states.csv || tail -1 {RESULT}/states.csv; true')
                self.record(阶段='180秒竞争负载／数据校验',最近观测=progress)
                if '包装已结束' in progress:complete=True;break
                time.sleep(15)
            require(complete,'观察超时，保留业务，不自动终止')
            require(self.ssh(f'cat {DIRECTORY}/launcher.exit').strip()=='0','业务包装失败')
        finally:
            # 即使业务失败，仍逐项归档；不关闭客机或终止业务。
            errors=[]
            for remote in (DIRECTORY,RESULT):
                try:self.command(scp+['-r',f'femu@127.0.0.1:{remote}',str(self.output)],120)
                except Exception as error:errors.append(str(error))
            try:(self.output/'源码身份.after.sha256').write_text(self.ssh(query))
            except Exception as error:errors.append(str(error))
            (self.output/'归档状态.json').write_text(json.dumps(dict(完整=not errors,错误=errors),ensure_ascii=False,indent=2))
        require(not errors,'归档不完整，保留客机')
        self.record(阶段='原始记录已归档，独立核验请求、候选竞争和搬运账目')
        result=analyze(self.output/LABEL)
        (self.output/'独立核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.shutdown()
        self.password=None
        self.record(阶段='180秒功能验证及独立核验完成，新客机正常关机',已完成=['竞争负载功能验证'],当前客机PID=None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args();preflight()
    if args.check:print('只读检查通过，未创建镜像');return
    password=getpass.getpass('本轮新客机认证（仅驻留内存）：')
    output=PROJECT/'research/rrzc/results'/LABEL;output.mkdir()
    pair=ModelRun(SimpleNamespace(label=LABEL,seconds=180,space_weight=50),password,output)
    try:pair.run()
    except Exception as error:
        pair.record(阶段='停止，证据和客机保留，未自动重跑',错误=str(error));raise


if __name__=='__main__':main()
