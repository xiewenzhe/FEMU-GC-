"""对齐真实停写、flush和维护日志；时间重合只作关联，不冒充阻塞因果。"""
from collections import Counter
import re
from analyze_sst_survival import stamp


def analyze_stalls(rock, zen):
    waits, rr, flush, pending_rr = [], [], [], {}
    counters = Counter()
    for line in zen.splitlines():
        if not re.match(r'\d{4}/\d\d/\d\d-', line):
            continue
        when = stamp(line)
        m = re.search(r'RR 开始：Zone (\d+)，', line)
        if m:
            pending_rr[m[1]] = when
        m = re.search(r'RR 完成：Zone (\d+) 已重置', line)
        if m and m[1] in pending_rr:
            rr.append((pending_rr.pop(m[1]), when))
        if '空间紧急分配等待：' in line:
            waits.append(when)
        if 'ZC 统计：' in line:
            counters['ZC完成'] += 1
    warnings = []
    for line in rock.splitlines():
        if not re.match(r'\d{4}/\d\d/\d\d-', line):
            continue
        when = stamp(line)
        for text, name in (('Stopping writes because', '停写告警'), ('Stalling writes because', '缓写告警')):
            if text in line:
                counters[name] += 1
                warnings.append((when, name))
        m = re.search(r'Flush lasted (\d+) microseconds', line)
        if m:
            duration = int(m[1])
            flush.append(dict(结束微秒=when, 持续微秒=duration,
                              区间内分配等待登记数=sum(when-duration <= t <= when for t in waits),
                              与已完成RR区间重合数=sum(a <= when and b >= when-duration for a, b in rr)))
    return dict(告警计数=dict(counters), 已配对RR区间数=len(rr), 未完成RR开始数=len(pending_rr),
                分配等待登记数=len(waits), Flush完成数=len(flush),
                最长十次Flush=sorted(flush, key=lambda r: r['持续微秒'], reverse=True)[:10],
                停缓写附近两秒有分配等待登记数=sum(any(abs(t-w) <= 2000000 for w in waits) for t, _ in warnings),
                边界='Flush开始由结束时间减日志耗时估计；记录时间可能延迟，区间重合/相邻不证明锁或分配等待因果；告警条数不是停顿持续时间')
