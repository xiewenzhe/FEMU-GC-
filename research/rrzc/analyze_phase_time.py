"""请求窗口按区间并集闭合；内部嵌套时间只报重叠，禁止累加成收益。"""
from array import array
from bisect import bisect_right
from collections import defaultdict
import csv
import json
from pathlib import Path
import struct
from analyze_reproducibility import request_rows
from ycsb_af import require, digest

KINDS = {1:'显式Flush调用',2:'后台排空等待',3:'GC检查及等待',4:'布局采集',
         5:'普通Zone分配',6:'迁移目标分配',7:'Open令牌等待',8:'ZoneReset调用',
         9:'ZoneFinish调用',10:'有效extent迁移调用',11:'读请求严格RR服务',
         12:'紧急分配恢复区间',13:'RR检查及执行'}


def merge(intervals):
    out = []
    for a,b in sorted(intervals):
        if b <= a: continue
        if out and a <= out[-1][1]: out[-1] = (out[-1][0],max(b,out[-1][1]))
        else: out.append((a,b))
    return out


def overlap(intervals, starts, ends):
    total = 0
    for a,b in merge(intervals):
        i = bisect_right(ends,a)
        while i < len(starts) and starts[i] < b:
            total += max(0,min(b,ends[i])-max(a,starts[i])); i += 1
    return total


def analyze(root,concurrent=False):
    starts, ends = array('Q'), array('Q')
    latencies = [array('Q'),array('Q')]
    api_ns = 0
    for row in request_rows(root/'请求提交.bin'):
        require(row[4] <= row[5] <= row[6] and (not ends or ends[-1] <= row[4]),'API时间线交叠或倒退')
        starts.append(row[4]); ends.append(row[5]); elapsed = row[5]-row[4]
        api_ns += elapsed; latencies[row[1]].append(elapsed)
    require(starts,'请求缺失'); first,last = starts[0],ends[-1]
    # 吞吐原口径以最后一条记录release为止；保留这个差值，使时间账与原分析一致。
    release = row[6]; last = release
    raw = (root/'等待区间.bin').read_bytes()
    require(raw[:16] == b'RRZCTM01'+bytes(8) and (len(raw)-16)%48 == 0,'计时证据格式错误或丢弃记录')
    records = list(struct.iter_unpack('<6Q',raw[16:]))
    require(records and all(r[0] in KINDS and r[1] <= r[2] for r in records),'计时记录不完整')
    groups = defaultdict(list); outside = []; sequence_counts = defaultdict(int)
    for kind,a,b,tid,obj,completed in records:
        if completed != (1<<64)-1:
            require(kind in (1,2,3,4) and completed <= len(starts),'前台检查点请求序号非法')
        if b <= first or a >= last: continue
        a,b = max(first,a),min(last,b); groups[kind].append((a,b))
        if kind in (1,2,3,4): outside.append((a,b)); sequence_counts[kind] += 1
    required=(1,4,5) if concurrent else (1,2,3,4,5,6,8,10,11,13)
    require(all(k in groups for k in required),'关键路径计时缺失')
    if concurrent:require(3 not in groups and 2 not in groups,'业务窗口意外包含批次GC门控或排空等待')
    outer = merge(outside); outer_ns = sum(b-a for a,b in outer)
    require(overlap(outer,starts,ends) == 0,'显式等待与API区间重叠，不能直接分账')
    remaining = last-first-api_ns-outer_ns
    require(remaining >= 0,'请求窗口时间账不闭合')
    details = []
    for kind in sorted(groups):
        intervals = groups[kind]; union = merge(intervals)
        all_ns = sum(b-a for a,b in union); api_overlap = overlap(union,starts,ends)
        wait_overlap = overlap(union,[x[0] for x in outer],[x[1] for x in outer])
        details.append(dict(类别=KINDS[kind],记录数=len(intervals),
            调用累计秒=sum(b-a for a,b in intervals)/1e9,区间并集秒=all_ns/1e9,
            与API重叠秒=api_overlap/1e9,与显式等待重叠秒=wait_overlap/1e9,
            其他区间重叠秒=(all_ns-api_overlap-wait_overlap)/1e9,
            最大单次秒=max(b-a for a,b in intervals)/1e9))
    by_kind = {r['类别']:r for r in details}
    seconds=lambda kind:by_kind.get(KINDS[kind],{}).get('区间并集秒',0.0)
    partition = dict(请求窗口秒=(last-first)/1e9,API累计秒=api_ns/1e9,
        显式Flush秒=seconds(1),后台排空秒=seconds(2),
        GC检查及等待秒=seconds(3),布局采集秒=seconds(4),
        显式等待并集秒=outer_ns/1e9,其他请求间隔秒=remaining/1e9,
        请求数=len(starts),吞吐ops=len(starts)/((last-first)/1e9))
    # 四类前台作用域互不嵌套；否则必须先归因交叠，不能把类别求和当窗口闭合。
    require(sum(b-a for a,b in outside) == outer_ns,'显式等待类别互相重叠')
    latency = []
    for role,values in zip(('READ','WRITE'),latencies):
        vals = sorted(values); n = len(vals)
        latency.append(dict(操作=role,次数=n,平均微秒=sum(vals)/n/1000,
            P99微秒=vals[min(n-1,int(n*.99))]/1000,
            P999微秒=vals[min(n-1,int(n*.999))]/1000,
            P9999微秒=vals[min(n-1,int(n*.9999))]/1000,
            最大毫秒=vals[-1]/1e6,超过1毫秒=sum(x>1000000 for x in vals),
            超过100毫秒=sum(x>100000000 for x in vals)))
    return dict(通过=True,前后台重叠=concurrent,窗口分账=partition,内部区间=details,API延迟=latency,
        输入SHA256={n:digest(root/n) for n in ('请求提交.bin','等待区间.bin')},
        说明='内部区间可嵌套且跨线程，只能比较并集/重叠；时间重叠不等于全部由该操作造成的阻塞。'
             '其他请求间隔含生成数据、请求记录、统计、调度及未观测开销，不能统一称后台等待。')


def finish(output):
    dest = output/'时间账目分析'; dest.mkdir()
    results = {t:analyze(output/'逐臂采证'/t) for t in ('p1','m1')}
    rows = [dict(臂=t,**r['窗口分账']) for t,r in results.items()]
    for name,data in (('窗口时间分账.csv',rows),
        ('内部区间重叠.csv',[dict(臂=t,**d) for t,r in results.items() for d in r['内部区间']]),
        ('API延迟.csv',[dict(臂=t,**d) for t,r in results.items() for d in r['API延迟']])):
        with (dest/name).open('x',newline='') as f:
            writer=csv.DictWriter(f,list(data[0]));writer.writeheader();writer.writerows(data)
    (dest/'时间账目核验.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    reports = {t:json.loads((output/(t+'-核验.json')).read_text()) for t in results}
    text = ['# 普通放置与协同放置：维护量和时间账目','',
        '同一新程序、同一固定逻辑输入，一臂普通K0＋Greedy、一臂协同K1＋Greedy。'
        '原12窗口受控业务，关闭缓存/WAL；每2048写显式Flush，不代表正常并发性能。','',
        '|指标|普通放置p1|协同放置m1|','|---|---:|---:|']
    for label,get in (
        ('总维护迁移MiB',lambda t:sum(reports[t]['维护'][k] for k in ('ZC迁移字节','RR迁移字节'))/(1<<20)),
        ('请求窗口秒',lambda t:results[t]['窗口分账']['请求窗口秒']),
        ('吞吐ops/s',lambda t:results[t]['窗口分账']['吞吐ops']),
        *[(k,lambda t,k=k:results[t]['窗口分账'][k]) for k in
          ('API累计秒','显式Flush秒','后台排空秒','GC检查及等待秒','布局采集秒','其他请求间隔秒')]):
        text.append(f'|{label}|{get("p1"):.6f}|{get("m1"):.6f}|')
    text += ['', '完整内部区间与API长尾见同目录CSV。仅一对完整运行；不将时间差当统计显著性，'
        '不把内部嵌套计时相加。布局采集是观察开销，不算业务收益。宿主资源见根目录宿主连续资源.jsonl。',
        '', '自动分账完成，具体收益归因需结合维护账目与宿主采样解释；不得自动认定少复制必然带来等比例延迟收益。']
    (dest/'结果分析.md').write_text('\n'.join(text)+'\n')
    return results
