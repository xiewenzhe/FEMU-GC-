#!/usr/bin/env python3
"""受控场景的选区维护价值核验；不把读速率命中或潜在RR搬运当作净收益。"""
import csv
import json
from pathlib import Path
import re
import sys
from datetime import datetime
from statistics import mean
from analyze_v1_shadow_stream import analyze, fields, require, PREFIX, MAX, SECOND
from analyze_v1_trajectory import read_samples, point_from_candidate, short_estimate, cross, identity


def event_kind(events):
    if not events:
        return "观察结束仍未知"
    kind=min(events,key=lambda x:x[0])[1]
    return {"RR":"发生独立RR，有可合并维护的观测依据",
            "ZC":"先被原Greedy选作空间ZC，未清理反事实受干预",
            "退役":"候选文件先退役，退役前未见独立RR"}[kind]


def stamp(line):
    match=PREFIX.match(line)
    require(match is not None,"事件缺少日志时间")
    return datetime.strptime(match[1],"%Y/%m/%d-%H:%M:%S.%f").timestamp()


def analyze_value(run):
    base=analyze(run)
    require(base["受控负载配置"]=="maintenance-cases","不是受控维护场景")
    samples,_=read_samples(run/"recent.csv")
    with (run/"recent.csv").open() as stream:
        host_times={}
        for row in csv.DictReader(stream):
            host_times[(int(row["Zone"]),int(row["设备快照纳秒"]))]=int(row["查询结束纳秒"])
    lines=(run/"zenfs.run.log").read_text().splitlines()
    origins=[]
    for line in lines:
        if (match:=re.search(r"滚动更新提交：代号=\d+，经过纳秒=(\d+)",line)):
            origins.append(stamp(line)-int(match[1])/SECOND)
    require(origins and max(origins)-min(origins)<.020,"日志与测试单调时钟未稳定对齐")
    origin=mean(origins)
    decisions={d["轮次"]:d for d in base["影子决策"]}
    files=[]
    for number,line in enumerate(lines,1):
        if "V1影子文件：" in line:
            files.append(dict(fields(line,"V1影子文件："),行号=number))
    outcomes=[]
    for c in base["候选观测"]:
        decision=decisions[c["轮次"]]
        if decision["存在Greedy"]!="1" or c["Zone"]==decision["实际Greedy"] or c.get("原候选资格")!="1" or c.get("统计准入")!="1":
            continue
        source_files=[f for f in files if f["轮次"]==c["轮次"] and f["Zone"]==c["Zone"]]
        hot=[f for f in source_files if re.search(r"/g\d+\.hot\.anchor\.sst$",f["文件名"])]
        require(sum(int(f["区内有效字节"]) for f in source_files)==int(c["有效字节"]),"候选文件覆盖不符")
        greedy=next(x for x in base["候选观测"] if x["轮次"]==c["轮次"] and x["Zone"]==decision["实际Greedy"])
        extra=max(0,int(c["有效字节"])-int(greedy["有效字节"]))
        start=decision["行号"]
        end=len(lines)
        for number,line in enumerate(lines[start:],start+1):
            if (match:=re.search(r"Zone 重置诊断：Zone=(\d+)，状态=成功，",line)) and match[1]==c["Zone"]:
                end=number
                break
        pending_ids={f["文件编号"] for f in source_files}
        events=[]
        retired=set()
        rr_valid=0
        for number,line in enumerate(lines[start:end],start+1):
            if (match:=re.search(r"RR 开始：Zone (\d+)，达阈值物理块 \d+，有效数据 (\d+) 字节",line)) and match[1]==c["Zone"]:
                events.append((number,"RR",line))
                rr_valid=int(match[2])
            if "简单双因素实际集合：" in line:
                chosen=fields(line,"简单双因素实际集合：")
                if chosen.get("Zone")==c["Zone"] and chosen.get("实际选中")=="1":
                    events.append((number,"ZC",line))
            if "V1后续文件退役：" in line:
                deleted=fields(line,"V1后续文件退役：")
                if deleted["文件编号"] in pending_ids:
                    retired.add(deleted["文件编号"])
                    if retired==pending_ids:
                        events.append((number,"退役",line))
        first=min(events,key=lambda x:x[0]) if events else None
        point=point_from_candidate(c)
        records=samples[point["zone"]]
        short=short_estimate(records,point)
        # 截断于首次RR/ZC选择或最后链接删除；维护复制自身的读取不能当作未来业务风险。
        if first:
            cutoff=(stamp(first[2])-origin)*SECOND-20_000_000
            before=[r for r in records if host_times[(point["zone"],r["time"])]<cutoff]
        else:
            before=records
        same=[]
        for row in before:
            if row["time"]<=point["time"]:
                continue
            if identity(row)!=identity(point):
                break
            same.append(row)
        mapping=[]
        for f in hot:
            g=int(re.search(r"/g(\d+)\.hot",f["文件名"])[1])
            mapping.append({"文件编号":f["文件编号"],"文件":f["文件名"],"代号":g,
                            "测试场景":("持续热读","停止但仍有效","停止后提前退役")[g%3]})
        item={"轮次":c["轮次"],"Zone":c["Zone"],"有效MiB":int(c["有效字节"])/(1<<20),
              "Greedy有效MiB":int(greedy["有效字节"])/(1<<20),"额外搬运MiB":extra/(1<<20),
              "额外预算合格":extra<=16*(1<<20),"热文件场景":mapping,"当前RC":point["rc"],
              "现有预测秒":None if int(c["预测达阈值纳秒"])==MAX else int(c["预测达阈值纳秒"])/SECOND,
              "约一秒过去数据预测":short,"后续维护价值观测":event_kind(events),
              "首次维护或退役前同块最大已观测RC":max([point["rc"]]+[r["rc"] for r in same]),
              "首次维护或退役前跨阈值观测":cross(before,point),
              "后续独立RR源有效MiB":rr_valid/(1<<20),"已验证净维护收益":False}
        prior=[point]+same
        item["首次维护或退役前同块观测长度秒"]=(prior[-1]["time"]-point["time"])/SECOND
        item["十秒内未跨阈值已确认"]=(prior[-1]["time"]-point["time"]>=10*SECOND and
            all(r["rc"]<4096 for r in prior) and
            all(b["rc"]>=a["rc"] for a,b in zip(prior,prior[1:])))
        if item["首次维护或退役前跨阈值观测"].get("原因","").startswith("业务结束前"):
            item["首次维护或退役前跨阈值观测"]["原因"]="首次维护或退役前未观测到跨越；不外推其后的未清理反事实"
        if first:
            item.update(首次事件行号=first[0],首次事件距离决策秒=stamp(first[2])-stamp(lines[start-1]))
        outcomes.append(item)
    stops=[]
    expiry=[]
    for number,line in enumerate(lines,1):
        if "受控热读停止：" in line:
            stops.append({"行号":number,"原始日志":line})
        if "受控文件退役完成：" in line:
            expiry.append({"行号":number,"原始日志":line})
    require(any("代号=1，" in x["原始日志"] for x in stops) and
            any("代号=2，" in x["原始日志"] for x in stops) and
            len(expiry)==1 and "代号=2，" in expiry[0]["原始日志"],"受控停止或退役事件缺失")
    return {"核验通过":True,"实际选择仍Greedy":True,"实际改选次数":0,
            "候选维护价值":outcomes,"实际停止事件":stops,"实际提前退役事件":expiry,
            "完整业务核验":base,
            "说明":"场景标签只用于事后分析，不输入机制。RR源搬运是潜在重叠依据，不是改选后的净收益；提前删除最后链接仍需结合已释放读句柄及Reset核对。"}


if __name__=="__main__":
    require(len(sys.argv)==2,"需要本轮原始目录")
    print(json.dumps(analyze_value(Path(sys.argv[1])),ensure_ascii=False,indent=2))
