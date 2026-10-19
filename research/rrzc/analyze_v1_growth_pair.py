#!/usr/bin/env python3
"""独立重算同块增长准入；复用原固定计划、数据完整性及成本核验。"""
import json
from pathlib import Path
import sys
from analyze_v1_pair import analyze_run as base_analyze, compare
from analyze_v1_execution_smoke import decision_check
from analyze_v1_shadow_stream import fields, predict, require, SECOND, MAX, BUDGET


def check_gate(current, previous):
    """已知增量必须能由同身份前后点重算；未知时不允许改选。"""
    known=previous["增量可知"]=="1"
    passed=previous["增长准入"]=="1"
    require(previous["增量可知"] in ("0","1") and previous["增长准入"] in ("0","1"),"增长标志非法")
    if not known:
        require(not passed,"未知增量却许可改选")
        return False
    require(current and current["统计准入"]=="1" and previous["有前点"]=="1","增长已知缺少原统计准入或前点")
    require(current["设备会话"]==previous["前会话"] and current["PPA"]==previous["前PPA"],"同块增量跨会话或PPA")
    for now,old in (("设备代际","前代际"),("块编号","前块编号"),("代际起点纳秒","前代际起点纳秒")):
        require(current[now]==previous[old],"同块增量跨身份或Reset")
    require(int(previous["前区编号"])==int(current["Zone"])//(256<<20),"前采样区编号不符")
    require(int(previous["前标志"]) & 1 and int(current["窗口标志"]) & 1,"采样未启用")
    interval=int(current["快照纳秒"])-int(previous["前快照纳秒"])
    delta=int(current["当前RC"])-int(previous["前RC"])
    require(SECOND//2<=interval<=2*SECOND and delta>=0,"采样过短、过旧或计数回退")
    require(interval==int(previous["间隔纳秒"]) and delta==int(previous["真实增量"]),"同块真实增量或间隔重算不符")
    numerator=max(0,4096-int(current["当前RC"]))*interval
    eta=min(MAX,(numerator+delta-1)//delta) if delta else MAX
    expected=delta>0 and eta<=10*SECOND
    require(eta==int(previous["短期达阈值纳秒"]) and passed==expected,"增长门槛重算不符")
    return expected


def analyze_run(run):
    text=(run/"zenfs.run.log").read_text()
    require(text.count("V1增长准入：已启用")==1 and "V1增长采样：" in text,"未确认采样门槛开启")
    gates={}; proposals={}
    for line in text.splitlines():
        if "V1增长候选：" in line:
            g=fields(line,"V1增长候选："); key=(int(g["轮次"]),int(g["Zone"]))
            require(key not in gates,"增长候选重复")
            gates[key]=g
        if "V1增长决策：" in line:
            d=fields(line,"V1增长决策："); key=int(d["轮次"])
            require(key not in proposals,"增长决策重复")
            proposals[key]=d
    seen=set(); gate_records=[]

    def validator(simple,blocks,decision):
        round_id=int(decision["轮次"]); require(round_id in proposals,"增长决策缺失")
        require(round_id not in seen,"决策重复使用")
        seen.add(round_id)
        eligible=[c for c in simple if 0<int(c["有效字节"])<=int(c["容量字节"]) and
            int(c["容量字节"])-int(c["有效字节"])>=BUDGET and c["覆盖资格"]=="1" and c["空目标资格"]=="1"]
        usable={z:b for z,b in blocks.items() if b.get("统计准入")=="1"}
        greedy,proposed,would,_,eta,_=predict(eligible,usable)
        old=proposals[round_id]
        require(tuple(int(old[k]) for k in ("原建议Zone","原会改选","原预测达阈值纳秒"))==
                (proposed or 0,int(would),eta),"原未过滤建议重算不符")
        admitted={}
        for c in simple:
            zone=int(c["Zone"]); key=(round_id,zone)
            require(key in gates,"原候选缺增长记录")
            gate=gates[key]; passed=check_gate(blocks.get(zone),gate)
            if zone in usable and (zone==greedy or passed): admitted[zone]=usable[zone]
            gate_records.append(dict(gate))
        # 原检查器独立重算预算、候选排序及实际集合；只提供准入后的其他候选。
        switched=decision_check(simple,admitted,decision)
        require(old["准入后建议Zone"]==decision["建议Zone"] and
                old["准入后会改选"]==decision["会改选"],"准入与执行建议不符")
        return switched

    out=base_analyze(run,decision_validator=validator)
    require(seen==set(proposals) and len(gate_records)==len(gates),"存在未核验的增长决策或候选")
    out["增长候选"]=gate_records
    out["原建议改选次数"]=sum(int(d["原会改选"]) for d in proposals.values())
    out["增长准入后建议改选次数"]=sum(int(d["准入后会改选"]) for d in proposals.values())
    samples=[fields(line,"V1增长采样：") for line in text.splitlines() if "V1增长采样：" in line]
    out["采样查询次数"]=len(samples)
    out["采样查询失败或超时次数"]=sum(s["可用"]!="1" for s in samples)
    out["采样查询平均微秒"]=sum(int(s["查询纳秒"]) for s in samples)/len(samples)/1000
    return out


if __name__=="__main__":
    require(len(sys.argv) in (2,3),"提供一个结果目录核验，或Greedy/V1两个目录比较")
    first=analyze_run(Path(sys.argv[1]))
    result=first if len(sys.argv)==2 else compare(first,analyze_run(Path(sys.argv[2])))
    print(json.dumps(result,ensure_ascii=False,indent=2))
