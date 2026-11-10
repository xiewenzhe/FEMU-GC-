"""独立编译并验收目标备选观察；只做设备功能测试，不启动完整业务。"""
import argparse
import getpass
import json
from pathlib import Path
import shutil
import signal
import sys
import time
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_placement_wmig_build_host as build
from run_placement_survivor_build_host import verify_history, check_label
from placement_target_shadow_source import prepare
from analyze_target_shadow import parse

LABEL='joint-controlled-placement-wmig-ws-tsb1-20261109'
JOB=Path(__file__).resolve().parents[1]/'results/placement-ablation-20261105/阶段六/第九步-同次目标影子实现/构建排队'
WAIT_FOR=Path('/home/data/xwz/rrzc-baselines/joint-related-compare-bootstrap-oaza-v1-20261109-results/comparison-state.json')


def resources():
    resources=build.preflight();resources['目录已占字节']=build.check_baseline_budget(8<<30)
    build.require(resources['data可用字节']>=42<<30 and resources['可用内存字节']>=30<<30,'原资源保护线不足')
    return resources


def execute(args,password,resource):
    output=build.IMAGE_DIR/(LABEL+'-results');build.require(not output.exists(),'不覆盖已有构建')
    output.mkdir();build.prepare_source=prepare
    payload=build.prepare(output)
    frozen=output/'观测入口源码';frozen.mkdir()
    for name in (__file__,Path(__file__).with_name('placement_target_shadow_source.py'),
                 Path(__file__).with_name('test_target_shadow.py'),
                 Path(__file__).parents[1]/'analyze_target_shadow.py'):
        source=Path(name);shutil.copyfile(source,frozen/source.name)
    build.stamp(frozen/'SHA256.json',{p.name:build.digest(p) for p in frozen.iterdir()})
    build.stamp(output/'预注册计划.json',dict(仅观测=True,完整业务=False,资源=resource,
        开关='ZENFS_RRZC_TARGET_SHADOW=1仅设备小测开启',原模式与安全正文保持=True,
        边界='普通部分区扫描建议；无预留/Finish反事实，不评价性能收益'))
    if args.detach and not build.detach_supervisor(output):return
    try:
        build.execute(SimpleNamespace(label=LABEL,mode='G2_WSURVIVOR',reuse_program=None),password,output,payload)
        verify_history(output,LABEL)
        directory=output/LABEL/'历史准入功能'
        rows=[]
        for name in ('history.zenfs.log','recovered.zenfs.log'):
            parsed=parse((directory/name).read_text())
            build.require(not parsed['格式化失败'],'设备目标观察格式化失败')
            rows.extend(parsed['逐次'])
        build.require(bool(rows),'设备未采集普通备选建议')
        build.require(any(r['路径']=='新写' for r in rows) and any(r['路径']=='必要迁移' for r in rows),
                      '设备缺少新写或必要迁移观察')
        build.stamp(output/'目标备选设备核验.json',dict(通过=True,逐次=rows,
            完整业务=False,设备安全与内容核验=True,
            边界='设备功能不保证收益；可能仅有不完整的普通扫描建议'))
        return output
    finally:password=None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true');parser.add_argument('--detach',action='store_true')
    parser.add_argument('--wait-idle',action='store_true',help='最多等待30分钟，不叠加或停止其他任务')
    args=parser.parse_args()
    check_label(LABEL)
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    if args.check:print(json.dumps(resources(),ensure_ascii=False));return
    if not args.wait_idle:
        resource=resources();password=getpass.getpass('目标备选独立构建认证（仅内存）：')
        try:execute(args,password,resource)
        finally:password=None
        return
    build.require(not JOB.exists(),'不覆盖已有构建排队')
    # 只冻结来源身份；等待期间若实现变化则停止，不能默默构建另一份代码。
    files=[Path(__file__),Path(__file__).with_name('placement_target_shadow_source.py'),
           Path(__file__).with_name('test_target_shadow.py'),
           Path(__file__).parents[1]/'model/rrzc_target_shadow.h',
           Path(__file__).parents[1]/'analyze_target_shadow.py']
    identity={str(p):build.digest(p) for p in files}
    password=getpass.getpass('排队构建认证（仅内存，最多等待30分钟）：')
    JOB.mkdir(parents=True)
    def stamp(**changes):
        path=JOB/'状态.json';current=json.loads(path.read_text()) if path.exists() else {}
        current.update(changes,更新时间UTC=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
        build.stamp(path,current)
    stamp(阶段='等待独立任务归档退出，未启动本轮客机',完整业务=False,等待来源=str(WAIT_FOR),
          构建标签=LABEL,来源SHA256=identity,等待上限秒=1800)
    if args.detach and not build.detach_supervisor(JOB):return
    stamp(监督进程PID=__import__('os').getpid())
    try:
        deadline=time.monotonic()+1800
        while True:
            try:
                waiting=json.loads(WAIT_FOR.read_text())
                if waiting.get('stage') in ('running','waiting','building_corrected_common_client'):
                    raise ValueError('已知独立任务尚未归档完成')
                resource=resources();break
            except ValueError as error:
                if str(error) not in ('已有本项目客机，不叠加','8101已占用','已知独立任务尚未归档完成'):raise
                stamp(阶段='等待资源，未叠加客机',最近保护原因=str(error))
            if time.monotonic()>=deadline:raise TimeoutError('等待30分钟仍未空闲；本轮未启动客机')
            time.sleep(10)
        build.require(all(build.digest(Path(p))==h for p,h in identity.items()),'排队期间源码变化，停止构建')
        stamp(阶段='资源已空闲，开始独立完整构建及设备功能')
        output=execute(SimpleNamespace(detach=False),password,resource)
        stamp(阶段='完整构建及设备验收通过，客机已归档退出',完成=True,原始结果=str(output))
    except BaseException as error:
        stamp(阶段='排队或构建停止，保留原始证据',完成=False,错误=repr(error));raise
    finally:password=None


if __name__=='__main__':main()
