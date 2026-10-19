#!/usr/bin/env python3
"""只读比较决策前可见因素；不优化门槛，不把后续标签输入准入。"""
import json
from pathlib import Path
import re
import sys
from statistics import mean
from collections import Counter
from analyze_guarded_stream import analyze,compare
from analyze_cost_observe import analyze as analyze_cost
from analyze_stream import rows
from analyze_v1_shadow_stream import require,fields,SECOND,MAX
from analyze_v1_trajectory import read_samples,identity,point_from_candidate,window_eta
from analyze_v1_victim_value import stamp


def past_window(samples,point,cutoff,seconds):
    require(seconds in (2,5),"仅比较预注册2/5秒窗口")
    available=[r for r in samples if r["time"]<point["time"] and r["returned"]<cutoff]
    target=point["time"]-seconds*SECOND
    previous=next((r for r in reversed(available) if r["time"]<=target),None)
    if previous is None:return {"可知":False,"原因":"缺少完整过去窗口"}
    span=point["time"]-previous["time"]
    if span>(seconds+1)*SECOND:return {"可知":False,"原因":"端点过旧"}
    segment=[r for r in available if r["time"]>=previous["time"]]+[point]
    if any(identity(r)!=identity(point) for r in segment):
        return {"可知":False,"原因":"窗口内会话、Reset或最大块身份变化"}
    intervals=[(b["time"]-a["time"],b["rc"]-a["rc"]) for a,b in zip(segment,segment[1:])]
    if any(t<=0 or t>2*SECOND or d<0 for t,d in intervals):
        return {"可知":False,"原因":"采样空档、时间或RC不单调"}
    complete=[(t,d) for t,d in intervals if t>=SECOND//4]
    if not complete:return {"可知":False,"原因":"没有足够长度的持续性间隔"}
    delta=point["rc"]-previous["rc"]
    predicted=(0 if point["rc"]>=4096 else min(MAX,((4096-point["rc"])*span+delta-1)//delta) if delta else MAX)
    return dict(可知=True,实际区间秒=span/SECOND,真实增量=delta,速度次每秒=delta*SECOND/span,
        预测纳秒=predicted,完整间隔数=len(complete),正增量间隔数=sum(d>0 for _,d in complete),
        每完整间隔均有读取=all(d>0 for _,d in complete),
        最早快照纳秒=previous["time"],最后过去快照纳秒=segment[-2]["time"],
        最大已返回时点纳秒=max(r["returned"] for r in segment[:-1]),可用截止纳秒=cutoff)


def gate_features(f):
    # 仅固定条件比较；这里不接收文件标识、运行类型或未来事件。
    ready=f["原约束通过"]
    w2,w5=f["约两秒过去观察"],f["约五秒过去观察"]
    sustained=lambda w:w["可知"] and w["每完整间隔均有读取"]
    recent=lambda e:e is not None and e<=10*SECOND
    return {"原准入":ready,"加2秒持续性":ready and sustained(w2),
        "加5秒持续性":ready and sustained(w5),
        "加5秒平均速率紧迫度":ready and w5["可知"] and recent(w5["预测纳秒"]),
        "加10秒平均速率紧迫度":ready and recent(f["十秒ETA纳秒"]),
        "加30秒平均速率紧迫度":ready and recent(f["三十秒ETA纳秒"])}


def extract(run,base):
    lines=(run/"zenfs.run.log").read_text().splitlines();growth={};pool={};summaries={}
    points,origins={},[];pressure=None
    wanted={(d["轮次"],d["短期建议Zone"]) for d in base["决策"] if d["原会改选"]=="1"}
    for number,line in enumerate(lines,1):
        if (m:=re.search(r"GC 检查：空闲=(\d+) 字节，非空闲=(\d+) 字节，空闲比例=(\d+)%",line)):
            pressure=dict(空闲字节=int(m[1]),非空闲字节=int(m[2]),空闲比例=int(m[3]))
        if (m:=re.search(r"滚动更新提交：代号=\d+，经过纳秒=(\d+)",line)):
            origins.append(stamp(line)-int(m[1])/SECOND)
        if "V1影子候选：" in line:
            b=fields(line,"V1影子候选：")
            if "当前RC" in b and (b["轮次"],b["Zone"]) in wanted:
                require(origins and max(origins)-min(origins)<.020,"决策前日志时钟未稳定对齐")
                # 此值只用此前标记；不借用后续时钟或未来轨迹修补对齐。
                cutoff=int((stamp(line)-mean(origins))*SECOND)-20_000_000
                require(pressure is not None,"候选缺同轮空间压力")
                points[(b["轮次"],b["Zone"])]=(b,cutoff,pressure)
        if "V1增长候选：" in line:
            g=fields(line,"V1增长候选：");growth[g["轮次"],g["Zone"]]=g
        if "独立约束预留：" in line:
            p=fields(line,"独立约束预留：");pool[p["轮次"]]=p
        if "独立约束候选：" in line:
            c=fields(line,"独立约束候选：");summaries[c["轮次"],c["Zone"]]=c
    samples,_=read_samples(run/"recent.csv")
    times={(int(r["Zone"]),int(r["设备快照纳秒"])):int(r["查询结束纳秒"]) for r in rows(run/"recent.csv")}
    for zone,values in samples.items():
        for r in values:r["returned"]=times[zone,r["time"]]
    decisions={d["轮次"]:d for d in base["决策"]};result=[]
    for o in base["建议候选后续观测"]:
        d=decisions[o["轮次"]];b,cutoff,space=points[o["轮次"],str(o["Zone"])]
        g=growth[o["轮次"],b["Zone"]];p=pool[o["轮次"]]
        c=summaries[o["轮次"],b["Zone"]];greedy=summaries[o["轮次"],d["GreedyZone"]]
        f=dict(当前RC=int(b["当前RC"]),距阈值次数=4096-int(b["当前RC"]),
            近期真实增量=int(g["真实增量"]),近期实际间隔秒=int(g["间隔纳秒"])/SECOND,
            原短期ETA纳秒=int(d["预测达阈值纳秒"]),十秒真实增量=int(b["十秒真实增量"]),
            三十秒真实增量=int(b["三十秒真实增量"]),十秒ETA纳秒=window_eta(b,10),三十秒ETA纳秒=window_eta(b,30),
            有效MiB=int(c["有效字节"])/(1<<20),Greedy有效MiB=int(greedy["有效字节"])/(1<<20),
            额外有效MiB=int(d["原额外有效字节"])/(1<<20),片段数=int(c["片段数"]),
            当前空间压力=space,逻辑回收MiB=256-int(c["有效字节"])/(1<<20),
            相对Greedy回收损失MiB=(int(c["有效字节"])-int(greedy["有效字节"]))/(1<<20),
            物理下界字节=int(c["总下界字节"]),物理上界字节=int(c["总上界字节"]),
            预留尾部MiB=int(p["剩余字节"])/(1<<20),预留有效MiB=int(p["有效字节"])/(1<<20),
            原约束通过=d["复核后可改选"]=="1",有效量历史变化=None,
            约两秒过去观察=past_window(samples[o["Zone"]],point_from_candidate(b),cutoff,2),
            约五秒过去观察=past_window(samples[o["Zone"]],point_from_candidate(b),cutoff,5))
        gates=gate_features(f)
        # 准入完成后才附加后续标签。它不是收益、准确率或可避免搬运预测。
        kind=o["后续首次事件"]["事件"] if o["后续首次事件"] else "窗口内未知"
        label=("已观测独立RR" if kind=="独立RR开始" else
            "已确认停读误报" if o["短期预测判定"]=="误报" else
            "先退役截断" if kind=="候选文件最后链接全部删除" else "其他或未知")
        result.append(dict(来源=str(run),轮次=o["轮次"],诊断文件=[r["文件名"] for r in o["候选文件"]],
            选择前因素=f,固定条件准入=gates,事后核验=dict(类别=label,首次事件=o["后续首次事件"],
                十秒预测判定=o["短期预测判定"],同块跨阈值=o["阈值跨越观测"],参与准入=False)))
    return result


def run(project):
    root=project/"research/rrzc/results";results=[];pairs=[]
    for family in ("stream","cases"):
        paths=[root/f"guarded-{family}-{s}-20261014"/f"guarded-{family}-{s}-20261014" for s in ("greedy","guarded")]
        a,b=(analyze(p) for p in paths);pair=compare(a,b)
        require(a["实际改选次数"]==0,"不能从被真实改选干预的组提取未清理后果")
        results.extend(extract(paths[0],a))
        moves=[dict(诊断文件=name,Greedy迁移MiB=value["Greedy"]["迁移MiB"],
            独立执行迁移MiB=value["独立约束"]["迁移MiB"],
            Greedy维护类型=value["Greedy"]["维护类型"],独立维护类型=value["独立约束"]["维护类型"],
            净迁移变化MiB=value["迁移变化MiB"]) for name,value in pair["同名锚点维护对照"].items()
            if value["Greedy"] is not None and value["独立约束"] is not None]
        require(sum(r["净迁移变化MiB"] for r in moves)==pair["总迁移变化MiB"],
            "已配对文件没有解释全部净迁移差，不能完整归因")
        pairs.append(dict(负载=family,RR减少=pair["RR减少次数"],ZC增加=pair["ZC增加次数"],
            总迁移变化MiB=pair["总迁移变化MiB"],逐文件维护与净迁移=moves,
            原始数据核验=True,解释="RR换成ZC但同一份有效数据仍只复制一次时，没有该文件迁移节省。"))
    require(len(results)==6,"不是两份预注册观察的六次原建议")
    summaries={}
    for name in results[0]["固定条件准入"]:
        retained=Counter(r["事后核验"]["类别"] for r in results if r["固定条件准入"][name])
        dropped=Counter(r["事后核验"]["类别"] for r in results if not r["固定条件准入"][name])
        summaries[name]=dict(保留原建议数=sum(retained.values()),保留后续观测类别=dict(retained),
            放弃后续观测类别=dict(dropped),已验证真实新策略收益=False)
    cost=analyze_cost(root/"cost-observe-stream-maintenance-cases-20261014-continued"/"short-shadow-cases-20261014")
    costs=[dict(轮次=c["轮次"],有效MiB=int(c["有效字节"])/(1<<20),
        十秒净减少MiB=int(c["约十秒有效量变化"]["减少字节"])/(1<<20),
        三十秒净减少MiB=int(c["约三十秒有效量变化"]["减少字节"])/(1<<20),
        两窗口可知=all(c[k]["历史可知"]=="1" for k in ("约十秒有效量变化","约三十秒有效量变化")),
        后续事件=c["后续观测"]["后续首次事件"],参与其他运行准入=False) for c in cost["建议候选成本"]]
    return dict(核验通过=True,性质="固定离线可观察因素诊断，未修改机制或启动实验",
        候选数=len(results),选择前因素与独立后续=results,固定条件比较=summaries,
        真实配对成本旁证=pairs,独立有效量观察旁证=dict(来源=cost["来源"],候选=costs),
        尚未实现=["2/5秒500ms测试观察未接生产选择器","本轮没有新增同块持续性门槛"],
        说明="六条建议是小样本且经过原准入筛选；保留机会不等于节省RR或净搬运。文件身份及后续退役/维护只用于事后核验。")


if __name__=="__main__":
    require(len(sys.argv)==1,"只核验固定既有结果，不接受事后参数扫描")
    print(json.dumps(run(Path(__file__).resolve().parents[2]),ensure_ascii=False,indent=2))
