"""等待已知ColdMap整组退出后执行一对采证；不抢其两臂间隙或停止其他任务。"""
import getpass
import json
import os
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_target_shadow_pair_host as entry
from report_target_shadow_pair import report

ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'results/placement-ablation-20261105/阶段六/第十步-同次目标完整采证'
JOB=STAGE/'排队监督'
WATCH=Path('/home/data/xwz/rrzc-baselines/joint-related-compare-bootstrap-coldmap-v1-20261110-results/comparison-state.json')


def main():
    require=entry.pair.common.require
    require(not JOB.exists(),'不覆盖已有队列')
    observed=json.loads(WATCH.read_text());watched=int(observed['supervisor_pid'])
    identity={str(p):entry.pair.common.digest(p) for p in [Path(__file__),
        Path(entry.__file__),ROOT/'analyze_target_shadow_pair.py',ROOT/'report_target_shadow_pair.py']}
    password=getpass.getpass('串行采证排队认证（仅内存）：')
    JOB.mkdir(parents=True)
    def stamp(**values):
        p=JOB/'状态.json';old=json.loads(p.read_text()) if p.exists() else {}
        old.update(values,更新时间UTC=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
        entry.pair.common.stamp(p,old)
    stamp(阶段='等待ColdMap整组归档退出，未启动本轮客机',等待来源=str(WATCH),
        等待监督PID=watched,本轮客机PID=None,源文件SHA256=identity,等待上限秒=7200)
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    if not entry.pair.common.detach_supervisor(JOB):return
    stamp(监督进程PID=os.getpid())
    old_getpass=getpass.getpass
    try:
        deadline=time.monotonic()+7200
        while True:
            proc=Path('/proc')/str(watched)
            try:command=(proc/'cmdline').read_bytes()
            except FileNotFoundError:command=b''
            active=b'run_coldmap_full_host.py' in command
            status=json.loads(WATCH.read_text())
            if not active and status.get('stage') not in ('running','waiting_for_shared_slot','building_corrected_common_client'):
                entry.pair.common.preflight();break
            require(time.monotonic()<deadline,'等待两小时仍未归档退出；本轮未启动客机')
            stamp(阶段='等待已知独立整组，未叠加客机',等待任务阶段=status.get('stage'),等待当前臂=status.get('current'))
            time.sleep(15)
        require(all(entry.pair.common.digest(Path(p))==h for p,h in identity.items()),'排队期间代码变化，停止而非运行其他版本')
        getpass.getpass=lambda _:password
        original=sys.argv;sys.argv=[entry.__file__]
        stamp(阶段='资源空闲，开始同程序串行双臂',配对标签=entry.PAIR_LABEL)
        try:entry.main()
        finally:sys.argv=original
        pair=entry.pair.common.IMAGE_DIR/(entry.PAIR_LABEL+'-results')
        conclusions=report(pair,STAGE)
        stamp(阶段='两臂及离线分析完成，客机已归档退出',完成=True,原始结果=str(pair),
            结论=conclusions,本轮客机PID=None)
    except BaseException as error:
        stamp(阶段='本轮停止推进，保留原始证据',完成=False,错误=repr(error));raise
    finally:getpass.getpass=old_getpass;password=None


if __name__=='__main__':main()
