#!/usr/bin/env python3
"""只读诊断两轮Greedy波动；按批次、分配等待和后台任务核对，不把时间重合当因果。"""
import argparse
from collections import Counter
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

SECOND = 10**9
BATCH = re.compile(r'RRZC批次完成：操作=(READ|WRITE)，批次=(\d+)，累计=(\d+)，热点起点=(\d+)，键校验=(\d+)，开始纳秒=(\d+)，完成纳秒=(\d+)')


def require(ok, why):
    if not ok:
        raise ValueError(why)


def stamp(line):
    # 同一客机日志时钟；不借用宿主时区解释跨日志的时间差。
    value = datetime.strptime(line.split()[0], '%Y/%m/%d-%H:%M:%S.%f')
    delta = value - datetime(1970, 1, 1)
    return (delta.days * 86400 + delta.seconds) * SECOND + delta.microseconds * 1000


def union_ns(intervals, begin=None, end=None):
    pieces = []
    for a, b in intervals:
        require(b >= a, '时间区间倒退')
        a = max(a, begin) if begin is not None else a
        b = min(b, end) if end is not None else b
        if b > a:
            pieces.append((a, b))
    total, right = 0, None
    for a, b in sorted(pieces):
        total += b - max(a, right) if right is not None and a < right < b else (
            b - a if right is None or a >= right else 0)
        right = b if right is None else max(right, b)
    return total


def paired(lines, start, finish):
    pending, rows = {}, []
    for i, line in enumerate(lines, 1):
        if start not in line and finish not in line:
            continue
        tid = line.split()[1]
        if start in line:
            require(tid not in pending, '同线程等待嵌套，不能安全配对')
            pending[tid] = (i, line)
        else:
            require(tid in pending, '恢复事件缺少开始')
            j, first = pending.pop(tid)
            a, b = stamp(first), stamp(line)
            require(b >= a, '事件时钟倒退')
            rows.append(dict(线程=tid, 开始行=j, 结束行=i, 开始纳秒=a, 结束纳秒=b,
                             持续秒=(b-a)/SECOND, 开始原文=first, 结束原文=line))
    require(not pending, '存在未闭合等待或维护')
    return rows


def read_csv(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def inspect(root):
    native = (root/'run.log').read_text()
    batches = {}
    for role, number, count, base, digest, begin, end in BATCH.findall(native):
        key = int(number), role
        require(key not in batches and int(end) >= int(begin), '批次重复或时间倒退')
        batches[key] = dict(累计=int(count), 热点=int(base), 键摘要=digest, 开始=int(begin), 结束=int(end))
    require(set(batches) == {(i, role) for i in range(24) for role in ('READ', 'WRITE')}, '批次覆盖不完整')
    zen = (root/'run.zenfs.log').read_text().splitlines()
    waits = paired(zen, '空间紧急分配等待：', '空间紧急分配恢复：')
    rr = paired(zen, 'RR 开始：', 'RR 完成：')
    offsets = [stamp(s)-int(re.search(r'服务时点纳秒=(\d+)', s)[1])
               for s in zen if 'RR公平队列服务：' in s]
    require(offsets and max(offsets)-min(offsets) < 1000000, '日志和单调时钟对齐误差达到1毫秒')
    offset = sorted(offsets)[len(offsets)//2]
    intervals = [(r['开始纳秒']-offset, r['结束纳秒']-offset) for r in waits]
    batch_rows = []
    for i in range(24):
        r, w = batches[i, 'READ'], batches[i, 'WRITE']
        a, b = min(r['开始'], w['开始']), max(r['结束'], w['结束'])
        batch_rows.append(dict(批次=i, 热点=r['热点'], 开始相对秒=(a-min(batches[0, x]['开始'] for x in ('READ','WRITE')))/SECOND,
                              读线程忙碌秒=(r['结束']-r['开始'])/SECOND, 写线程忙碌秒=(w['结束']-w['开始'])/SECOND,
                              批次跨度秒=(b-a)/SECOND, 等待较慢线程='WRITE' if w['结束'] > r['结束'] else 'READ',
                              分配等待重合秒=union_ns(intervals, a, b)/SECOND))
    rock = (root/'run.rocksdb.log').read_text().splitlines()
    events = [json.loads(s.split('EVENT_LOG_v1 ', 1)[1]) for s in rock if 'EVENT_LOG_v1 ' in s]
    job_threads = {}
    for s in rock:
        if 'EVENT_LOG_v1 ' in s:
            e = json.loads(s.split('EVENT_LOG_v1 ', 1)[1])
            if e['event'] == 'compaction_started':
                job_threads[e['job']] = s.split()[1]
    jobs, compactions, flushes = {}, [], []
    for e in events:
        kind = e['event']
        if kind == 'compaction_started':
            require(e['job'] not in jobs, '压缩任务编号重复')
            jobs[e['job']] = e
        elif kind == 'compaction_finished':
            start = jobs.pop(e['job'])
            a, b = start['time_micros']*1000, e['time_micros']*1000
            compactions.append(dict(任务=e['job'], 开始纳秒=a, 结束纳秒=b,
                日志耗时秒=e['compaction_time_micros']/1e6, CPU秒=e['compaction_time_cpu_micros']/1e6,
                输出字节=e['total_output_size'], 输出层=e['output_level'],
                同线程分配等待重合秒=union_ns([(r['开始纳秒'],r['结束纳秒']) for r in waits
                    if r['线程']==job_threads[e['job']]], a, b)/SECOND))
    require(not jobs, '存在未完成压缩')
    for s in rock:
        m = re.search(r'Flush lasted (\d+) microseconds, and (\d+) cpu microseconds', s)
        if m:
            b, duration = stamp(s), int(m[1])*1000
            flushes.append(dict(结束纳秒=b, 耗时秒=duration/SECOND, CPU秒=int(m[2])/1e6,
                                 分配等待重合秒=union_ns([(r['开始纳秒'], r['结束纳秒']) for r in waits], b-duration, b)/SECOND))
    loop_begin = min(batches[0, x]['开始'] for x in ('READ','WRITE'))
    loop_end = max(batches[23, x]['结束'] for x in ('READ','WRITE'))
    stage_begin, stage_end = [int((root/name).read_text()) for name in ('run.start.ns','run.end.ns')]
    require(stage_begin <= loop_begin <= loop_end <= stage_end, '脚本与批次单调时钟不闭合')
    loop = (loop_end-loop_begin)/SECOND
    stage = (stage_end-stage_begin)/SECOND
    native_seconds = float(re.search(r'^rrzcrotating\s*:.*? ([\d.]+) seconds ', native, re.M)[1])
    before, after = [json.loads((root/f'strict.{phase}.json').read_text()) for phase in ('before','business')]
    if (root/'真实准备读取.json').exists():
        before=json.loads((root/'真实准备读取.json').read_text())['设备后']
    sst = {r['文件']: r for r in read_csv(root/'布局-0.sst.csv')}
    summary = dict(脚本阶段秒=stage, 原生轮转秒=native_seconds, 批次首尾跨度秒=loop,
        首个批次前秒=(loop_begin-stage_begin)/SECOND, 最后批次后秒=(stage_end-loop_end)/SECOND,
        阶段减原生轮转秒=stage-native_seconds, 批次吞吐ops=1376256/loop,
        写线程较慢批次数=sum(r['等待较慢线程']=='WRITE' for r in batch_rows),
        前十九批跨度秒=sum(r['批次跨度秒'] for r in batch_rows[:19]),
        最后五批跨度秒=sum(r['批次跨度秒'] for r in batch_rows[19:]),
        分配等待次数=len(waits), 分配等待累计秒=sum(r['持续秒'] for r in waits),
        分配等待并集秒=union_ns([(r['开始纳秒'],r['结束纳秒']) for r in waits])/SECOND,
        分配等待与业务批次重合秒=union_ns(intervals,min(v['开始'] for v in batches.values()),max(v['结束'] for v in batches.values()))/SECOND,
        RR次数=len(rr), RR累计秒=sum(r['持续秒'] for r in rr),
        缓写告警数=sum('Stalling writes because' in s for s in rock),
        停写告警数=sum('Stopping writes because' in s for s in rock),
        压缩任务数=len(compactions), 压缩累计秒=sum(r['日志耗时秒'] for r in compactions),
        压缩CPU累计秒=sum(r['CPU秒'] for r in compactions), 压缩输出MiB=sum(r['输出字节'] for r in compactions)/2**20,
        最长压缩秒=max(r['日志耗时秒'] for r in compactions), 最长Flush秒=max(r['耗时秒'] for r in flushes),
        业务实际NAND读取增量=after['实际NAND读取']-before['实际NAND读取'],
        日志时钟对齐跨度微秒=(max(offsets)-min(offsets))/1000,
        初始SST数=len(sst), 初始LSM各层文件数=dict(Counter(r['层级'] for r in sst.values())))
    return dict(汇总=summary, 批次=batch_rows, 分配等待=waits, RR区间=rr, 压缩=compactions, Flush=flushes,
                初始SST=sst, 请求摘要=[[i,role,r['累计'],r['热点'],r['键摘要']] for (i,role),r in sorted(batches.items())])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    provenance = json.loads((args.source/'分析/分析来源.json').read_text())
    roots = list(args.source.glob('*/g1'))
    require(len(roots)==1, '来源目录不唯一')
    parent = roots[0].parent
    hashes = {}
    for tag in ('g1','g2'):
        for name in ('run.log','run.zenfs.log','run.rocksdb.log','布局-0.sst.csv','strict.before.json',
                     'strict.business.json','run.start.ns','run.end.ns','公共参数.txt','space.before.json'):
            p = parent/tag/name
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            require(provenance.get(str(p))==digest, '原始文件哈希不匹配：'+str(p))
            hashes[str(p)] = digest
    data = {tag: inspect(parent/tag) for tag in ('g1','g2')}
    require(data['g1']['请求摘要']==data['g2']['请求摘要'], '请求批次摘要不一致')
    require((parent/'g1/公共参数.txt').read_bytes()==(parent/'g2/公共参数.txt').read_bytes(), '公共参数不同')
    a, b = data['g1']['初始SST'], data['g2']['初始SST']
    require(a.keys()==b.keys(), '初始文件集合不同，不能按同名文件比较')
    changed = [name for name in a if any(a[name][k]!=b[name][k] for k in ('层级','最小键十六进制','最大键十六进制'))]
    args.output.mkdir(exist_ok=False)
    result = dict(各组=data, 同名初始SST键范围或层级不同数=len(changed), 来源SHA256=hashes,
                  边界='阶段减轮转包含打开、布局、flush、压缩等待和关闭，不能全部叫压缩等待；重合不证明因果；累计任务时间不等于前台阻塞时间。')
    (args.output/'核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for name, rows in [('汇总',[dict(组别=t,**d['汇总']) for t,d in data.items()]),
                       ('逐批次',[dict(组别=t,**r) for t,d in data.items() for r in d['批次']]),
                       ('分配等待',[dict(组别=t,**r) for t,d in data.items() for r in d['分配等待']]),
                       ('压缩任务',[dict(组别=t,**r) for t,d in data.items() for r in d['压缩']])]:
        with (args.output/(name+'.csv')).open('x',newline='') as f:
            writer = csv.DictWriter(f,fieldnames=list(rows[0]))
            writer.writeheader();writer.writerows(rows)
    print(json.dumps(dict(各组={k:v['汇总'] for k,v in data.items()}, 初始范围不同数=len(changed)),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
