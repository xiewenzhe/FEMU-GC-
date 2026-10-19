#!/usr/bin/env python3
"""核验固定390秒尾部功能观察；Finish、浪费空间、后续复制分别记账。"""
import json
from pathlib import Path
import re
import sys
from collections import Counter, defaultdict
from analyze_stream import analyze as base_analyze, rows
from analyze_short_shadow_stream import rounds
from analyze_cost_observe import inspect
from analyze_v1_shadow_stream import fields, require
from analyze_v1_trajectory import read_samples
from analyze_maintenance_overlap import PREFIX, RR_START

CAPACITY=256<<20
ACQUIRE=re.compile(r"迁移预留取得空区：Zone=(\d+)")
USE=re.compile(r"迁移使用专用预留：Zone=(\d+)，需要=(\d+)，剩余=(\d+)")
REPLACE=re.compile(r"迁移预留替换：旧Zone=(\d+)，新Zone=(\d+)")
RESET=re.compile(r"Zone 重置诊断：Zone=(\d+)，状态=成功，主机代际=(\d+)/(\d+)，")


def ledger(lines):
    generations=defaultdict(int); tails={}; mappings={}; pending={}; replacement=[]
    copies=[]; allocations=[]; source_finishes=[]; commits=[]; active={}; full_rollovers=[]
    current_reserve=None
    for number,line in enumerate(lines,1):
        prefix=PREFIX.match(line); thread=prefix[2] if prefix else "合成线程"
        if (m:=RESET.search(line)):
            zone,old,new=map(int,m.groups())
            require(new==old+1 and generations[zone]==old,"尾部账目Reset代际不一致")
            generations[zone]=new; tails[zone]=CAPACITY
            mappings={fid:p for fid,p in mappings.items() if not (p["Zone"]==zone and p["代际"]==old)}
        if (m:=ACQUIRE.search(line)):
            new=int(m[1])
            if current_reserve is not None and current_reserve!=new and tails.get(current_reserve)==0:
                full_rollovers.append(dict(行号=number,旧Zone=current_reserve,
                    旧代际=generations[current_reserve],新Zone=new,丢弃尾部字节=0))
            current_reserve=new; tails[current_reserve]=CAPACITY
        if (m:=RR_START.search(line)):
            active[thread]="RR"
        if "简单双因素实际集合：" in line:
            c=fields(line,"简单双因素实际集合：")
            if c["实际选中"]=="1": active[thread]="ZC"
        if (m:=REPLACE.search(line)):
            old,new=map(int,m.groups()); remaining=tails.get(old)
            require(current_reserve==old,"替换旧区不是当前已观测预留")
            files=[dict(p,文件编号=fid) for fid,p in mappings.items() if p["Zone"]==old and p["代际"]==generations[old]]
            replacement.append(dict(行号=number,旧Zone=old,旧代际=generations[old],新Zone=new,
                丢弃尾部字节=remaining,当时已追踪有效文件=files,
                当时已追踪有效字节=sum(p["有效字节"] for p in files),
                首次替换后复制=None,说明="已确认预留替换Finish；丢弃空间不是数据迁移量"))
            tails[old]=0; tails[new]=CAPACITY; current_reserve=new
        if "RR 封闭专用预留源：" in line:
            c=fields(line,"RR 封闭专用预留源："); zone=int(c["Zone"])
            source_finishes.append(dict(行号=number,Zone=zone,封闭尾部字节=int(c["封闭剩余"] .split(" 字节")[0])))
            tails[zone]=0; current_reserve=None
        if (m:=USE.search(line)):
            zone,need,remaining=map(int,m.groups())
            require(0<need<=remaining<=CAPACITY and zone==current_reserve,"预留申请容量或身份不符")
            if tails.get(zone) is not None:
                require(tails[zone]==remaining,"预留尾部与前次实际写入/Reset账目不符")
            tails[zone]=remaining
            require(zone not in pending,"前次预留申请缺少复制")
            allocation=dict(行号=number,Zone=zone,代际=generations[zone],需要字节=need,原尾部字节=remaining)
            allocations.append(allocation); pending[zone]=allocation
        if "成本预留观察：" in line:
            c=fields(line,"成本预留观察：")
            if c["可知"]=="1" and c["存在"]=="1":
                zone=int(c["Zone"])
                require(zone==current_reserve,"成本预留与实际资源池身份不符")
                if tails.get(zone) is not None:
                    require(tails[zone]==int(c["剩余字节"]),"成本快照与迁移尾部账目不符")
                tracked=sum(p["有效字节"] for p in mappings.values() if p["Zone"]==zone and p["代际"]==generations[zone])
                require(tracked==int(c["有效字节"]),"预留有效文件和有效量账目不符")
        if "V1后续迁移复制：" in line:
            c=fields(line,"V1后续迁移复制："); require(c["复制成功"]=="1","存在迁移失败")
            fid,source,target,length=map(int,(c["文件编号"],c["源Zone"],c["目标Zone"],c["有效字节"]))
            if fid in mappings:
                previous=mappings[fid]
                require((previous["Zone"],previous["代际"],previous["有效字节"])==
                        (source,generations[source],length),"重复文件复制的源区、代际或有效量不符")
            move=dict(c,行号=number,源代际=generations[source],目标代际=generations[target],维护类型=active.get(thread,"尚未确认"))
            copies.append(move)
            if target in pending:
                a=pending.pop(target)
                # 本负载固定直接写入，每文件单片段；验证申请量与真正复制量及写指针一致。
                require(a["需要字节"]==length and int(c["目标片段"])==target+CAPACITY-a["原尾部字节"],
                        "不符合本轮直接写单片段的物理申请和写指针")
                tails[target]=a["原尾部字节"]-length; a["实际复制字节"]=length; a["复制行号"]=number
            mappings[fid]=dict(Zone=target,代际=generations[target],有效字节=length,文件名=c["文件名"],最后复制行号=number)
            for e in replacement:
                if e["首次替换后复制"] is None and number>e["行号"] and target==e["新Zone"]:
                    e["首次替换后复制"]=move
        if "V1后续映射提交：" in line:
            c=fields(line,"V1后续映射提交："); require(c["成功"]=="1","映射提交失败"); commits.append(c)
        if "V1后续文件退役：" in line:
            c=fields(line,"V1后续文件退役："); mappings.pop(int(c["文件编号"]),None)
        if "RR 完成：" in line or "ZC 统计：" in line: active.pop(thread,None)
    require(not pending,"预留申请未完成复制")
    require(Counter(c["文件编号"] for c in copies)==Counter(c["文件编号"] for c in commits),"复制与映射提交总账不符")
    counts=Counter(); repeated=0; repeated_moves=[]
    for c in copies:
        if counts[c["文件编号"]]:
            repeated+=int(c["有效字节"]); repeated_moves.append(c)
        counts[c["文件编号"]]+=1
    for e in replacement:
        ids={str(p["文件编号"]) for p in e["当时已追踪有效文件"]}
        following=[c for c in copies if c["行号"]>e["行号"] and c["文件编号"] in ids and
                   int(c["源Zone"])==e["旧Zone"] and c["源代际"]==e["旧代际"]]
        e["旧区有效文件后续复制"]=following
        e["旧区有效文件后续复制字节"]=sum(int(c["有效字节"]) for c in following)
        e["说明"]+="；后续复制是实际事件，不等于相对另一victim的因果额外成本"
    return dict(预留申请=allocations,预留替换=replacement,自然写满后取得新预留=full_rollovers,
                预留源RR封闭=source_finishes,实际复制=copies,
                重复文件复制=repeated_moves,重复文件复制字节=repeated,
                可确认丢弃尾部字节=sum(e["丢弃尾部字节"] for e in replacement if e["丢弃尾部字节"] is not None))


def analyze(run):
    require(run.name=="tail-stream-390-20261014","不是本轮固定结果目录")
    require(rows(run/"config.csv")==[{"策略":"valid","计划业务秒数":"390","RR阈值":"4096","GC启动比例":"20",
        "回收预算字节":"201326592","读写是否并发":"1","预热秒数":"0","实验用途":"预留尾部功能观察"}],"预先固定参数改变")
    base=base_analyze(run); lines=(run/"zenfs.run.log").read_text().splitlines()
    require("V1真实决策：" not in "\n".join(lines),"开启真实改选")
    decisions,_,_=rounds(lines)
    alignment=rows(run.parent/"rrzc-tail-stream-390-20261014-tests/probe.csv")
    require(len(alignment)==1 and alignment[0]["读取数据页"]==alignment[0]["写入或重置"]=="0","只读接口探测不符")
    cost,samples=inspect(lines,{d["轮次"]:d for d in decisions},int(alignment[0]["对齐字节"]))
    _,batches=read_samples(run/"recent.csv"); require(len(batches)==780,"390秒500ms轨迹不完整")
    result=ledger(lines)
    require(sum(int(c["有效字节"]) for c in result["实际复制"])==base["全程维护"]["总维护迁移字节"],"实际复制与维护迁移总账不符")
    require(len(result["预留替换"])==sum("迁移预留替换：" in line for line in lines),"替换事件有遗漏")
    return dict(核验通过=True,来源=str(run),实际策略="Greedy",实际改选次数=0,固定业务秒数=390,
        尾部事件账目=result,成本候选=cost,有效量采样条数=len(samples),完整业务核验=base,
        已观察到尾部不足替换=bool(result["预留替换"]),已验证victim改选净收益=False,
        说明="单次预先固定功能观察，非独立策略配对或稳态。丢弃尾部是空间损失，不是迁移字节；是否后续再搬须看文件身份与Reset代际。")


if __name__=="__main__":
    require(len(sys.argv)==2,"需要本轮原始结果目录")
    print(json.dumps(analyze(Path(sys.argv[1])),ensure_ascii=False,indent=2))
