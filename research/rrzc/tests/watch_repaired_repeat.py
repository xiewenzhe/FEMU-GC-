#!/usr/bin/env python3
"""只读等待追加重复归档，自动核验四次Greedy；不启动客机或模型实验。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import time

from analyze_repro_repeats import analyze,load,save
from analyze_io_trace import compare
from cache_config_guard import check
from run_calibration_host import detach_supervisor

COUNTERS=('实际NAND读取','计入RC读取','自身刷新豁免读取',
          '其他块搬迁计数读取','阈值触发事件','被阻止读取')


def device_delta(arm):
    before=load(arm/'真实准备读取.json')['设备后']
    after=load(arm/'strict.business.json')
    delta={key:after[key]-before[key] for key in COUNTERS}
    if any(n<0 for n in delta.values()):raise ValueError('设备累计数倒退，不能跨会话相减')
    if after['启用']!=1 or after['历史最高RC']>1024:raise ValueError('严格RR配置或最高RC异常')
    return delta


def verify(previous,current,output):
    archives={}
    for root in (previous,current):
        run=load(root/'状态.json');info=run['镜像处理'];path=Path(info['archive'])
        if path.parent.resolve()!=root.resolve():raise ValueError('归档路径不属于对应实验')
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for data in iter(lambda:stream.read(1<<20),b''):h.update(data)
        if h.hexdigest()!=info['archive_sha256']:raise ValueError('实验归档哈希不符')
        archives[str(path)]=h.hexdigest()
    save(output/'归档哈希核验.json',archives)
    base=analyze(previous,current,output/'四次语义核验')
    sources=[root/load(root/'状态.json')['标签'] for root in (previous,current)]
    reference=sources[0]/'g1';compared={};deltas={};caches={}
    for number,source in enumerate(sources,1):
        for tag in ('g1','g2'):
            arm=source/tag;key=str(number)+'/'+tag
            deltas[key]=device_delta(arm)
            caches[key]=check((arm/'run.rocksdb.log').read_text(),1)
            if arm!=reference:
                compared[key]=compare(reference,arm)
    save(output/'跨轮逐请求设备首差.json',compared)
    save(output/'设备分类读取增量.json',deltas)
    save(output/'四次缓存核验.json',caches)
    io_same=all(row['设备摘要相同'] and row['稀疏反馈首差'] is None for row in compared.values())
    delta_same=all(row==next(iter(deltas.values())) for row in deltas.values())
    result=dict(验收通过=bool(base.get('受控跨轮验收通过')) and io_same and delta_same,
        语义验收=base,逐请求设备摘要及阈值反馈一致=io_same,设备分类读取增量一致=delta_same,
        边界='四次相同配置受控Greedy；逐请求摘要定位不等于全部NAND内存快照。设备增量窗口为真实准备读后至业务采集，含重新打开。正常并发和模型收益尚未验收。')
    save(output/'验收结论.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('previous',type=Path);parser.add_argument('current',type=Path)
    parser.add_argument('output',type=Path);parser.add_argument('--detach',action='store_true')
    args=parser.parse_args()
    if args.previous.resolve()==args.current.resolve():raise ValueError('不能比较同一轮')
    args.output.mkdir(parents=True,exist_ok=False)
    frozen=args.output/'分析源码';frozen.mkdir()
    here=Path(__file__).resolve()
    for source in (here,here.with_name('run_calibration_host.py'),
                   here.parent.parent/'analyze_repro_repeats.py',
                   here.parent.parent/'analyze_io_trace.py',here.with_name('cache_config_guard.py')):
        shutil.copyfile(source,frozen/source.name)
    if args.detach and not detach_supervisor(args.output):return
    status=dict(监督PID=os.getpid(),阶段='等待追加实验完整归档及退出',
        前轮=str(args.previous),本轮=str(args.current),输出=str(args.output),
        用途='仅读取日志并生成验收报告，不启动客机，不自动发布收益',最长等待秒=3600)
    def record(**values):status.update(values);save(args.output/'状态.json',status)
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    record()
    try:
        deadline=time.monotonic()+3600
        while time.monotonic()<deadline:
            stage=args.current/'阶段归档.json';state=args.current/'状态.json'
            if stage.is_file() and state.is_file():
                archived=load(stage);run=load(state)
                if not archived['客机仍存活'] and run['当前客机PID'] is None:
                    if not archived['完整'] or not archived['业务成功']:
                        raise ValueError('追加实验未成功归档，停止验收')
                    if run.get('目标诊断') and run.get('设备读摘要') is not None:
                        record(阶段='已归档退出，开始跨轮核验')
                        result=verify(args.previous,args.current,args.output)
                        record(阶段='验收通过' if result['验收通过'] else '验收未通过，保留首差',
                            验收通过=result['验收通过'],监督PID=None,已退出监督PID=os.getpid())
                        return
            time.sleep(10)
        raise TimeoutError('一小时内未获得完整结果，停止等待，原实验由其自身监督管理')
    except BaseException as error:
        record(阶段='自动核验停止',错误=repr(error),验收通过=False,
            监督PID=None,已退出监督PID=os.getpid())
        raise


if __name__=='__main__':main()
