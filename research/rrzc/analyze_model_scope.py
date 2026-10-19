#!/usr/bin/env python3
"""三组模型阶段诊断：核验机会与成本多样性，不拟合新权重或推断未发生的收益。"""
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path

from analyze_fixed_three_way import analyze_all
from analyze_v1_shadow_stream import fields, require
from analyze_v1_growth_stream import qualifies

MIB = 1 << 20


def decision_features(candidates, costs, decision):
    """只读取选区当时字段；后续RR、退役和搬运结果不参与特征计算。"""
    greedy = candidates[decision["GreedyZone"]]
    proposed = candidates[decision["短期建议Zone"]]
    gv,pv = int(greedy["有效字节"]),int(proposed["有效字节"])
    gr,pr = int(greedy["容量字节"])-gv,int(proposed["容量字节"])-pv
    require(gr>0 and pr>0,"没有正回收量")
    options=[]
    for zone,candidate in candidates.items():
        cost=costs[zone]
        if zone==decision["GreedyZone"] or not qualifies(candidate):
            continue
        if cost["短期预测可知"]!="1" or int(cost["短期达阈值纳秒"])>int(decision["界限纳秒"]):
            continue
        if max(0,int(candidate["有效字节"])-gv)>int(decision["预算字节"]):
            continue
        options.append(candidate)
    require(any(c["Zone"]==decision["短期建议Zone"] for c in options),"原建议不在准入集合")
    # 比较一种固定的成本优先排序；仅说明同一快照的排序是否有可选空间。
    ranked=min(options,key=lambda c:(Fraction(int(c["有效字节"]),int(c["容量字节"])-int(c["有效字节"])),
        int(costs[c["Zone"]]["短期达阈值纳秒"]),int(c["Zone"])))
    return dict(轮次=decision["轮次"],Greedy有效MiB=gv/MIB,建议有效MiB=pv/MIB,
        Greedy回收MiB=gr/MIB,建议回收MiB=pr/MIB,额外搬运MiB=(pv-gv)/MIB,
        Greedy每回收一字节需搬运=float(Fraction(gv,gr)),建议每回收一字节需搬运=float(Fraction(pv,pr)),
        成本比值=float(Fraction(pv,pr)/Fraction(gv,gr)),预算内紧迫候选数=len(options),
        成本优先排序改变建议=ranked["Zone"]!=decision["短期建议Zone"],
        原预测秒=int(decision["预测达阈值纳秒"])/1e9)


def summarize_arm(arm):
    candidates={};costs={};features=[]
    for line in (Path(arm["来源"])/"zenfs.run.log").read_text().splitlines():
        if "简单双因素候选：" in line:
            c=fields(line,"简单双因素候选：");candidates[c["Zone"]]=c
        if "独立约束候选：" in line:
            c=fields(line,"独立约束候选：");costs[c["Zone"]]=c
        if "独立约束决策：" in line:
            d=fields(line,"独立约束决策：")
            require(set(candidates)==set(costs),"选区前成本字段缺失")
            if d["原会改选"]=="1":
                features.append(decision_features(candidates,costs,d))
            candidates={};costs={}
    require(not candidates and not costs and len(features)==arm["原短期建议次数"],"建议记录未闭合")
    combinations=Counter((f["Greedy有效MiB"],f["建议有效MiB"],f["预算内紧迫候选数"]) for f in features)
    return dict(建议数=len(features),决策特征=features,
        成本组合=[dict(Greedy有效MiB=g,建议有效MiB=p,预算内紧迫候选数=n,出现次数=count)
                  for (g,p,n),count in sorted(combinations.items())],
        成本优先改变建议数=sum(f["成本优先排序改变建议"] for f in features),
        后续预测观测=dict(Counter(o.get("短期预测判定","未知") for o in arm["建议候选后续观测"])),
        后续首次事件=dict(Counter((o.get("后续首次事件") or {}).get("事件","未知") for o in arm["建议候选后续观测"])),
        解释="成本优先仅在原快照同一准入集合中比较，非新策略执行；不能重演后续轨迹或据此保证收益。")


def run():
    # 重新核验原始请求、数据、决策及账目，不直接信任已有结论文件。
    result=analyze_all()
    arms={name:summarize_arm(result[name]) for name in ("Greedy","保留尾部约束","关闭尾部约束")}
    return dict(原始三组重新核验通过=True,阶段="模型可辨识性与收益来源诊断",各组=arms,
        逐文件收益摘要={name:{k:v for k,v in x["逐文件成本"].items() if k!="文件"}
                      for name,x in result["相对Greedy"].items()},
        已修改生产机制=False,已拟合参数=False,
        结论边界="未来跨阈值仅在未改选Greedy组评价；统计来自当前固定负载，不能推广到任意业务或声称理论收益上限。")


if __name__=="__main__":
    print(json.dumps(run(),ensure_ascii=False,indent=2))
