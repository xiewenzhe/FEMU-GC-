#!/usr/bin/env python3
"""串行Greedy只读采证；仅重编译受控客户端，不部署期望成本模型或自动长测。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import signal

import run_joint_host as base
import run_persistent_cases_host as cases
from run_joint_host import require,PROJECT,IMAGE_DIR
from run_window_credit_host import resources,digest
from analyze_expected_rr_probe import analyze

LABEL='joint-rr-probability-probe-v1-20261022'


class ProbeRun(cases.CaseRun):
    def root(self,command,timeout=45):
        prefix=f'nohup setsid bash {self.target}/run_persistent_credit_guest.sh '
        if command.startswith(prefix):
            require(command.startswith(prefix+'greedy '),'只读采证禁止启用其他选择策略')
            command=command.replace(prefix,
                f'nohup setsid env RRZC_JOINT_TRACE=1 bash {self.target}/run_persistent_credit_guest.sh ',1)
        return super().root(command,timeout)

    def arm(self,scenario,mode='greedy'):
        require(mode=='greedy','本轮只允许Greedy采证')
        super().arm(scenario,mode)
        out=self.output/(scenario+'-greedy')
        result=analyze(out/scenario,scenario)
        with (out/'概率采证核验.json').open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
        self.record(阶段=scenario+'只读轨迹和原维护验收完成，客机已归档退出',
            轨迹批次=result['采样批次'],阈值标注=result['状态计数'],概率标签样本数=result['数值概率且标签可知样本数'])


def interrupted(*_):raise KeyboardInterrupt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default=LABEL);parser.add_argument('--check',action='store_true')
    parser.add_argument('--detach',action='store_true');args=parser.parse_args()
    args.phase='build';args.scenarios=[]
    base.PARENT=cases.PARENT;base.PARENT_MANIFEST=cases.MANIFEST
    output,profile=base.preflight(args);before=resources(profile);cases.checked_source()
    for scenario in cases.CASES:
        for path in (IMAGE_DIR/f'{args.label}-{scenario}-greedy.qcow2',Path(f'/tmp/{args.label}-{scenario}-greedy.pid'),
            Path(f'/tmp/{args.label}-{scenario}-greedy.qmp'),Path(f'/tmp/{args.label}-{scenario}-greedy-ssh.sock')):
            require(not path.exists(),'目标已存在，拒绝覆盖：'+str(path))
    if args.check:
        print(json.dumps(dict(只读预检通过=True,未启动客机=True,资源=before),ensure_ascii=False));return
    password=getpass.getpass('Greedy只读采证客机认证（仅驻留内存）：')
    output.mkdir();(output/'平台配置.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2))
    (output/'启动入口.py').write_bytes(Path(__file__).read_bytes())
    plan=dict(目的='先核验同一物理块真实阈值到达，再决定是否采用概率模型；不是改选收益或性能实验',
        FEMU二进制SHA256=digest(PROJECT/'build-femu/qemu-system-x86_64'),父镜像=str(cases.PARENT),
        入口SHA256=digest(Path(__file__)),场景=list(cases.CASES),策略='全部Greedy',初始资源=before,
        观测='客户端默认关闭旁观者独立开启，每500毫秒保存已有C2全区响应；无NAND数据读、不注入RC',
        不变='4GiB、128MiB区、RR1024、GC20%、原读取计划、候选资格、原库及旧模型全部不改',
        标注='完整同块同代际连续观察；本轮ZC、RR、文件退役、最大块改变、Reset或采样空档立即删失',
        边界='仿真NAND仍逐客机重建；不拿qcow2称内存快照，不以三个受控场景宣称统计校准',
        收尾='复用已验收逐阶段完整哈希归档、正常关机和PID/SSH退出；失败停止，不自动重跑或启动YCSB')
    (output/'预注册计划.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    if args.detach:
        read,write=os.pipe();pid=os.fork()
        if pid:
            os.close(write);require(os.read(read,1)==b'1','监督分离失败');os.close(read)
            print(f'监督PID={pid}，状态={output / "状态.json"}',flush=True);return
        os.close(read);os.setsid()
        fd=os.open(output/'监督.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        os.dup2(fd,1);os.dup2(fd,2);os.dup2(os.open(os.devnull,os.O_RDONLY),0)
        os.write(write,b'1');os.close(write)
    signal.signal(signal.SIGTERM,interrupted)
    run=ProbeRun(args,password,output,profile);run.qemu_digest=plan['FEMU二进制SHA256']
    run.state['模式顺序']=[s+'-greedy' for s in cases.CASES]
    try:
        run.build()
        for scenario in cases.CASES:run.arm(scenario)
        run.record(阶段='三个Greedy只读采证完成，全部客机关闭，未启用新模型或运行新YCSB')
    except BaseException as error:
        run.record(阶段='停止，不自动重跑',错误=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__);raise
    finally:run.password=None


if __name__=='__main__':main()
