"""等待已存在的独立对照退出，再串行构建和配对；不接管或停止其他任务。"""
import getpass
import json
from pathlib import Path
import subprocess
import signal
import sys
import time
import traceback
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_survivor_shadow_host as entry
from run_calibration_host import detach_supervisor
from analyze_survivor_outcomes import analyze as outcomes

ROOT=Path(__file__).resolve().parents[1]
JOB=ROOT/'results/placement-ablation-20261105/阶段六/第五步-旧策略影子归因/队列任务-v2'
WAIT_FOR=Path('/home/data/xwz/rrzc-baselines/joint-related-compare-full-v2-20261109-results/comparison-state.json')


def stamp(**updates):
    path=JOB/'状态.json'
    old=json.loads(path.read_text()) if path.exists() else {}
    old.update(updates,更新时间UTC=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
    entry.build_entry.stamp(path,old)


def wait_existing():
    until=time.monotonic()+1800
    while time.monotonic()<until:
        observed=json.loads(WAIT_FOR.read_text())
        processes=subprocess.run(['ps','-C','qemu-system-x86_64','-o','args='],capture_output=True,text=True).stdout
        busy=str(entry.build_entry.IMAGE_DIR)+'/' in processes
        stage=observed.get('stage')
        # 等待现有整组监督结束，避免在其两臂间隙抢占端口或磁盘。
        if not busy and stage not in ('running','waiting','preparing'):
            entry.build_entry.preflight();return
        stamp(阶段='等待现有整组对照退出，尚未启动本轮客机',
              等待任务阶段=stage,等待任务当前臂=observed.get('current'),本轮当前客机PID=None)
        time.sleep(10)
    raise RuntimeError('等待现有任务超过30分钟；本轮未强占资源，队列退出，需核验现场后再启动')


def summarize(pair,output,proof):
    reports={m:json.loads((pair/m/'业务核验.json').read_text()) for m in ('G2',entry.MODE)}
    lines=['# 旧G2影子归因：自动完成记录','',
        '影子只运行原Choose，不申请目标令牌/不Commit。建议可行不等于最终分配成功；本轮新规则不变。','',
        '|指标|G2|W历史准入|','|---|---:|---:|']
    for title,key,unit in [('总有效复制MiB','总迁移字节',1<<20),('RR次数','RR次数',1),('ZC次数','ZC次数',1)]:
        lines.append('|'+title+'|'+'|'.join(f"{r['维护及资源']['维护'][key]/unit:.3f}" for r in reports.values())+'|')
    shadow=reports[entry.MODE]['旧G2影子']
    lines+=['',f"实际拒绝{reports[entry.MODE]['W历史准入']['拒绝次数']}次；其中覆盖原入池建议{shadow['拒绝覆盖原入池建议数']}次，原建议已回退{shadow['拒绝但原建议已回退数']}次，未知建议{shadow['未知建议数']}次。",'',
        '必须结合首次分歧、末端空间、逐笔复制及旧W后续链判断；本文件不宣布总量差的算法因果收益，也不以单对吞吐差宣称显著性。','',
        '原始结果：`'+str(pair)+'`。影子逐次表、起点请求、迁移/空间/API/端到端指标保存在原始配对分析。',
        '首次分歧及拒绝后续链见本目录`实际去向与首差核验.json`和对应CSV。']
    (output/'自动完成记录.md').write_text('\n'.join(lines)+'\n')


def main():
    if JOB.exists():raise RuntimeError('已有队列目录，不覆盖或重复启动')
    password=getpass.getpass('排队构建与一组影子配对认证（仅内存）：')
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    JOB.mkdir(parents=True)
    stamp(阶段='排队监督准备',等待来源=str(WAIT_FOR),算法改变=False,本轮当前客机PID=None)
    if not detach_supervisor(JOB):return
    # 与原detached监督相同：凭据只在当前独立进程内存，不写argv/环境/脚本/日志。
    entry.getpass.getpass=lambda _:password
    stamp(监督进程PID=__import__('os').getpid())
    try:
        wait_existing()
        stamp(阶段='资源已空闲，开始独立构建',构建标签=entry.BUILD_LABEL)
        entry.run_build(SimpleNamespace(label=entry.BUILD_LABEL,detach=False,check=False))
        stamp(阶段='构建与设备通过，开始同程序串行两臂',配对标签=entry.PAIR_LABEL)
        entry.run_pair(SimpleNamespace(label=entry.PAIR_LABEL,detach=False,check=False))
        pair=entry.build_entry.IMAGE_DIR/(entry.PAIR_LABEL+'-results')
        output=JOB.parent/'完整配对';proof=outcomes(pair,output)
        summarize(pair,output,proof)
        stamp(阶段='构建、设备、串行配对及离线链完成，客机已退出',完整实验完成=True,
              原始配对=str(pair),派生证据=str(output),本轮当前客机PID=None)
    except BaseException as error:
        stamp(阶段='队列或本轮执行停止，保留原始证据',完整实验完成=False,错误=repr(error))
        traceback.print_exc();raise
    finally:
        password=None;entry.getpass.getpass=lambda _:(_ for _ in ()).throw(RuntimeError('本轮认证已释放'))


if __name__=='__main__':main()
