#!/usr/bin/env python3
"""只读回放最小尾部约束；不接入选择器，不用事后目标或文件寿命修补输入。"""
import json
from pathlib import Path
import sys
from collections import Counter
from analyze_cost_observe import analyze as analyze_cost, physical_bounds
from analyze_tail_stream import analyze as analyze_stream
from analyze_tail_pair import analyze as analyze_pair, compare
from analyze_stream import rows
from analyze_v1_shadow_stream import require

CAPACITY=256<<20


def candidate_bounds(candidate, alignment):
    parts=candidate.get("片段",[])
    if not parts or alignment<=0:
        return None
    bounds=[]
    for index,p in enumerate(parts,1):
        require(int(p["次序"])==index,"尾部约束片段顺序不连续")
        length=int(p["逻辑字节"])
        known,lower,upper=physical_bounds(length,alignment)
        if not known or upper>CAPACITY:
            return None
        bounds.append((lower,upper))
    if sum(int(p["逻辑字节"]) for p in parts)!=int(candidate["有效字节"]):
        return None
    return dict(首下界=bounds[0][0],首上界=bounds[0][1],总下界=sum(b[0] for b in bounds),
                总上界=sum(b[1] for b in bounds),片段数=len(bounds))


def guard(greedy,proposed,reserve,alignment,alternative_fits=None):
    # alternative_fits只用于表达已独立确认的分支；现有真实日志始终传未知。
    require(alternative_fits is None or isinstance(alternative_fits,bool),"替代目标标志非法")
    g,c=candidate_bounds(greedy,alignment),candidate_bounds(proposed,alignment)
    out=dict(分类="未知回退",保留原建议=False,原因="片段物理界限或完整覆盖未知",
             Greedy物理界限=g,建议物理界限=c,预留快照=reserve,
             其他普通目标可完整容纳=alternative_fits,已确认实际Finish=False,
             已证明额外搬运=False,原RR紧迫度及额外有效预算不变=True)
    if not g or not c:
        return out
    if reserve.get("可知")!="1" or reserve.get("存在")!="1":
        out["原因"]="当前预留未知或不存在，不推断未来分配"; return out
    tail,valid=int(reserve["剩余字节"]),int(reserve["有效字节"])
    require(0<=tail<=CAPACITY and 0<=valid<=CAPACITY and tail+valid<=CAPACITY,"预留容量字段不符")
    out["尾部字节"]=tail
    # 这些范围只描述首片段走当前有数据预留且有替补的条件分支，不是预测。
    if valid:
        conditional=lambda b:[tail if b["首下界"]>tail else 0,tail if b["首上界"]>tail else 0]
        gr,cr=conditional(g),conditional(c)
        out["条件首片段丢弃范围字节"]=dict(Greedy=gr,建议=cr,
            相对增加范围=[cr[0]-gr[1],cr[1]-gr[0]],
            前提="首片段均走当前非空预留、替补可用；不表示真实目标或后续总成本")
    if c["总上界"]<=tail:
        out.update(分类="尾部不否决",保留原建议=True,原因="建议全部片段物理上界可容纳于当前尾部")
    elif not valid and c["总上界"]<=CAPACITY:
        out.update(分类="尾部不否决",保留原建议=True,原因="预留无有效数据，原分配路径可Reset复用，无旧数据Finish损失")
    elif alternative_fits is True:
        out.update(分类="尾部不否决",保留原建议=True,原因="其他普通部分目标已独立确认可完整容纳，不强制走专用预留")
    elif g["首上界"]<=tail<c["首下界"]:
        out.update(分类="预留压力保守拦截",原因="Greedy首片段确定可装入而建议确定装不入；保护当前尾部，不把替代目标未知当确定Finish")
    elif g["首下界"]<=tail<g["首上界"] and c["首下界"]>tail:
        out["原因"]="Greedy稀疏头边界未知，建议首片段确定装不入；不能确认额外成本，保守回退"
    elif c["首下界"]<=tail<c["首上界"]:
        out["原因"]="建议首片段位于稀疏头边界，实际申请量未知"
    elif g["首下界"]>tail and c["首下界"]>tail:
        out["原因"]="两者首片段均不适配；其他目标未知，不能确认相对Greedy新增尾部成本"
    else:
        out["原因"]="建议全部片段不能保证容纳，后续片段分配未知；不只检查第一个片段"
    return out


def replay_candidates(candidates,alignment):
    records=[]
    for c in candidates:
        if c["是影子建议"]!="1":
            continue
        matches=[g for g in candidates if g["轮次"]==c["轮次"] and g["是Greedy"]=="1"]
        require(len(matches)==1,"建议轮次缺少唯一Greedy成本")
        g=matches[0]
        require(c["原资格"]==g["原资格"]=="1" and c["当前预留"]==g["当前预留"],"建议资格或同轮预留快照不符")
        evaluation=guard(g,c,c["当前预留"],alignment)
        records.append(dict(轮次=c["轮次"],原GreedyZone=g["Zone"],原建议Zone=c["Zone"],
            原建议额外有效字节=int(c["额外有效字节"]),
            离线保留Zone=c["Zone"] if evaluation["保留原建议"] else g["Zone"],评估=evaluation))
        # 已完成护栏判断后才独立列出后续标签，绝不将其作为准入输入。
        if "后续观测" in c:
            outcome=c["后续观测"]
            records[-1]["事后独立核验"]=dict(短期预测判定=outcome["短期预测判定"],
                后续首次事件=outcome["后续首次事件"],参与护栏输入=False)
    return dict(原建议次数=len(records),分类计数=dict(Counter(r["评估"]["分类"] for r in records)),记录=records,
                实际新护栏执行次数=0,已验证收益=False)


def pair_input(pair):
    s=pair["首次选区前布局"]
    def candidate(length):
        return {"有效字节":s[length],"片段":[{"次序":"1","逻辑字节":s[length]}]}
    reserve={"可知":"1","存在":s["预留存在"],"Zone":s["预留Zone"],
             "剩余字节":s["预留尾部"],"有效字节":s["预留有效量"]}
    # 不使用后来复制的实际dense长度、Finish日志或校验结果缩窄当时界限。
    return candidate("冷长度"),candidate("热长度"),reserve


def run(project):
    results=project/"research/rrzc/results"
    cost_root=results/"cost-observe-stream-maintenance-cases-20261014-continued"
    cost=analyze_cost(cost_root/"short-shadow-cases-20261014")
    tail_root=results/"tail-stream-390-20261014"
    tail=analyze_stream(tail_root/"tail-stream-390-20261014")
    alignment=rows(tail_root/"rrzc-tail-stream-390-20261014-tests/probe.csv")
    require(len(alignment)==1,"尾部观察对齐记录不唯一")
    stream_replays=[dict(来源=cost["来源"],**replay_candidates(cost["候选成本"],cost["实际对齐字节"])),
                   dict(来源=tail["来源"],**replay_candidates(tail["成本候选"],int(alignment[0]["对齐字节"])))]
    gpath=results/"tail-pair-greedy-v3-20261014/tail-pair-greedy-v3-20261014"
    vpath=results/"tail-pair-v1-v3-20261014/tail-pair-v1-v3-20261014"
    g,v=analyze_pair(gpath),analyze_pair(vpath); comparison=compare(g,v)
    probe=rows(vpath.parent/"rrzc-tail-pair-v1-v3-20261014-tests/probe.csv")
    require(len(probe)==1 and probe[0]["读取数据页"]==probe[0]["写入或重置"]=="0","配对对齐探测不符")
    evaluation=guard(*pair_input(v),int(probe[0]["对齐字节"]))
    return dict(核验通过=True,性质="新约束只读离线回放，不是新策略实验",
        原始日志重新核验通过=True,滚动观察回放=stream_replays,
        单片段功能输入回放=dict(来源=str(vpath),输入仅来自首次选区前布局=True,评估=evaluation,
            事后实际=dict(提前Finish次数=1,丢弃尾部MiB=16,净迁移增加MiB=comparison["净迁移增加MiB"]),
            说明="事后实际独立报告，未参与护栏输入；此处原V1不与滚动短期建议混为同一策略"),
        已接入真实选区=False,已证明减少RR或总搬运=False,
        仍缺统计=["选择当时各候选片段的真实稀疏标志和精确物理申请量",
                  "其他普通部分目标当时的匹配与可用性；不能用事后实际目标代替",
                  "护栏拦截后丧失的RR合并机会及终态空间的真实配对代价"])


if __name__=="__main__":
    require(len(sys.argv)==1,"本轮仅回放已固定的原始结果，不接受事后调参数")
    print(json.dumps(run(Path(__file__).resolve().parents[2]),ensure_ascii=False,indent=2))
