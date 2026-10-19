#!/usr/bin/env python3
"""按同一物理块及设备代际定位阈值跨越；只做离线比较，不修改机制。"""
import csv
import json
from pathlib import Path
import sys
from collections import Counter, defaultdict
from analyze_v1_shadow_stream import analyze, require, SECOND, MAX


def identity(row):
    return tuple(row[k] for k in ("session_hi","session_lo","zone","generation","block_id",
                                  "ch","lun","plane","block","born"))


def cross(samples,point,threshold=4096):
    if point["rc"]>=threshold:
        return {"状态":"决策时已达到阈值","下界秒":0,"上界秒":0}
    previous=point
    for row in samples:
        if row["time"]<=point["time"]:
            continue
        if identity(row)!=identity(point):
            if (row["session_hi"],row["session_lo"])!=(point["session_hi"],point["session_lo"]):
                reason="设备会话变化"
            elif row["generation"]!=point["generation"] or row["born"]!=point["born"]:
                reason="Reset代际变化"
            else:
                reason="最大块身份变化"
            return {"状态":"未知","原因":reason}
        if row["rc"]<previous["rc"]:
            return {"状态":"未知","原因":"同块同代际RC倒退"}
        if row["rc"]>=threshold:
            return {"状态":"观测到跨阈值","下界秒":(previous["time"]-point["time"])/SECOND,
                    "上界秒":(row["time"]-point["time"])/SECOND,
                    "下界RC":previous["rc"],"上界RC":row["rc"],
                    "区间定义":"阈值跨越在下界之后、上界以内"}
        previous=row
    return {"状态":"未知","原因":"业务结束前未观测到跨越，不能认定不会RR"}


def short_estimate(samples,point):
    # 只使用决策之前的数据，不用事后热读速度回填预测。
    earlier=[r for r in samples if r["time"]<=point["time"]-SECOND]
    if not earlier:
        return {"状态":"未知","原因":"缺少一秒以前样本"}
    old=earlier[-1]
    span=point["time"]-old["time"]
    if span>2*SECOND:
        return {"状态":"未知","原因":"此前采样空档超过两秒"}
    relevant=[r for r in samples if old["time"]<=r["time"]<point["time"]]+[point]
    if any(identity(r)!=identity(point) for r in relevant):
        return {"状态":"未知","原因":"短期差分窗口内块身份或代际变化"}
    if any(b["rc"]<a["rc"] for a,b in zip(relevant,relevant[1:])):
        return {"状态":"未知","原因":"短期差分窗口内RC倒退"}
    delta=point["rc"]-old["rc"]
    predicted=0 if point["rc"]>=4096 else min(MAX,((4096-point["rc"])*span+delta-1)//delta) if delta else MAX
    return {"状态":"可计算","实际区间秒":span/SECOND,"同块真实增量":delta,
            "速度次每秒":delta*SECOND/span,"预测纳秒":predicted,
            "说明":"约一秒区间差分，不是设备提供的固定一秒滚动窗口"}


def read_samples(path):
    mapping={"设备会话高位":"session_hi","设备会话低位":"session_lo","Zone":"zone",
             "设备代际":"generation","块编号":"block_id","通道":"ch","LUN":"lun",
             "plane":"plane","block":"block","设备快照纳秒":"time","代际起点纳秒":"born","RC":"rc"}
    by_zone=defaultdict(list)
    batches=defaultdict(set)
    durations={}
    with path.open() as stream:
        for raw in csv.DictReader(stream):
            values={k:int(v) for k,v in raw.items()}
            batch=values["批次"]
            row={destination:values[source] for source,destination in mapping.items()}
            zone=values["区索引"]
            require(zone not in batches[batch],"同批采样重复Zone")
            batches[batch].add(zone)
            begin,end=values["查询开始纳秒"],values["查询结束纳秒"]
            require(begin<=end and values["RR阈值"]==4096,"采样查询时间或阈值不符")
            duration=end-begin
            require(batch not in durations or durations[batch]==duration,"同批查询耗时不一致")
            durations[batch]=duration
            require(row["zone"]==zone*(256<<20) and row["block"]==zone and
                    0<=row["ch"]<8 and 0<=row["lun"]<4 and 0<=row["plane"]<2 and
                    row["block_id"]==(row["ch"]*4+row["lun"])*2+row["plane"],"采样块编号或PPA不符")
            flags=values["窗口标志"]
            require(flags & 1 and not flags & ~15 and row["born"]<=row["time"] and
                    0<=values["十秒真实增量"]<=values["三十秒真实增量"]<=row["rc"],"采样状态或计数矛盾")
            age=row["time"]-row["born"]
            require(not flags & 2 or age>=10*SECOND,"采样十秒预热标志矛盾")
            require(not flags & 4 or (age>=30*SECOND and flags & 2 and not flags & 8),"采样三十秒窗口标志矛盾")
            old=by_zone[row["zone"]]
            require(not old or row["time"]>old[-1]["time"],"同区采样时间不递增")
            old.append(row)
    require(batches and sorted(batches)==list(range(len(batches))) and
            all(v==set(range(32)) for v in batches.values()),"采样批次不连续或不是完整32区")
    return by_zone,durations


def point_from_candidate(c):
    hi,lo=map(int,c["设备会话"].split("/"))
    ch,lun,plane,block=map(int,c["PPA"].split("/"))
    return dict(session_hi=hi,session_lo=lo,zone=int(c["Zone"]),generation=int(c["设备代际"]),
                block_id=int(c["块编号"]),ch=ch,lun=lun,plane=plane,block=block,
                born=int(c["代际起点纳秒"]),time=int(c["快照纳秒"]),rc=int(c["当前RC"]))


def window_eta(candidate,seconds):
    flags=int(candidate["窗口标志"])
    if int(candidate["当前RC"])>=4096:
        return 0
    bit,key=(2,"十秒真实增量") if seconds==10 else (4,"三十秒真实增量")
    if not flags & bit:
        return None
    delta=int(candidate[key])
    return min(MAX,((4096-int(candidate["当前RC"]))*seconds*SECOND+delta-1)//delta) if delta else MAX


def trajectory(run):
    base=analyze(run)
    with (run/"observer-config.csv").open() as stream:
        require(list(csv.DictReader(stream))==[{"采样间隔毫秒":"500"}],"不是预定500毫秒采样")
    samples,durations=read_samples(run/"recent.csv")
    decisions={d["轮次"]:d for d in base["影子决策"]}
    result=[]
    for c in base["候选观测"]:
        if c.get("统计准入")!="1" or c.get("原候选资格")!="1":
            continue
        point=point_from_candidate(c)
        records=samples[point["zone"]]
        actual=cross(records,point)
        short=short_estimate(records,point)
        modes={"现有较小窗口速度":int(c["预测达阈值纳秒"]) if c["预测可知"]=="1" else None,"仅十秒速度":window_eta(c,10),
               "仅三十秒速度":window_eta(c,30),
               "约一秒同块区间速度":short.get("预测纳秒")}
        comparison={}
        observed=None
        if actual["状态"] in ("观测到跨阈值","决策时已达到阈值"):
            low,high=actual["下界秒"],actual["上界秒"]
            if high<=10:
                observed=True
            elif low>=10:
                observed=False
            actual["是否严格十秒内跨越"]=observed
        greedy=decisions[c["轮次"]]["实际Greedy"]==c["Zone"]
        for name,value in modes.items():
            predicted=None if value is None else value<=10*SECOND
            judgement="未知或跨越区间横跨十秒界限"
            if observed is not None and predicted is not None:
                judgement="命中" if observed and predicted else "漏报" if observed else "误报" if predicted else "正确未报"
            comparison[name]={"预测秒":None if value in (None,MAX) else value/SECOND,
                              "无有限预测":value==MAX,"十秒内预测":predicted,"判定":judgement}
        result.append({"轮次":c["轮次"],"Zone":c["Zone"],"块编号":point["block_id"],
                       "设备代际":point["generation"],"当前RC":point["rc"],"实际Greedy源":greedy,
                       "阈值跨越观测":actual,"约一秒差分":short,"离线预测比较":comparison,
                       "解释":"预测只用决策前数据；Greedy源受实际清理干预，不能用于判断不清理时是否会RR"})
    counts={name:dict(Counter(r["离线预测比较"][name]["判定"] for r in result if not r["实际Greedy源"]))
            for name in ("现有较小窗口速度","仅十秒速度","仅三十秒速度","约一秒同块区间速度")}
    gaps=[(b["time"]-a["time"])/SECOND for records in samples.values() for a,b in zip(records,records[1:])]
    return {"核验通过":True,"来源":str(run),"实际策略仍为Greedy":True,"实际改选次数":0,
            "采样批次":len(durations),"采样最大间隔秒":max(gaps),
            "查询平均微秒":sum(durations.values())/len(durations)/1000,
            "查询最大微秒":max(durations.values())/1000,"候选跨越与预测":result,
            "仅非Greedy候选误报漏报计数":counts,"影子短测完整核验":base,
            "说明":"小样本诊断，不是预测器泛化结论、性能开销对照或真实改选收益验证"}


if __name__=="__main__":
    require(len(sys.argv)==2,"需要本轮原始结果目录")
    print(json.dumps(trajectory(Path(sys.argv[1])),ensure_ascii=False,indent=2))
