#!/usr/bin/env python3
"""只读核验真实V1改选、复制提交、Reset及数据比对，不计算净收益。"""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
from analyze_v1_shadow_stream import fields, predict, require, BUDGET, EXTRA, SECOND, MAX
from analyze_controlled_zc import ZC_STATS


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def decision_check(simple, blocks, decision):
    eligible=[]
    for c in simple:
        valid,capacity=(int(c[k]) for k in ("有效字节","容量字节"))
        qualifies=(0<valid<=capacity and capacity-valid>=BUDGET and
                   c["覆盖资格"]=="1" and c["空目标资格"]=="1")
        b=blocks.get(int(c["Zone"]))
        if b:
            require(int(b["原候选资格"])==int(qualifies),"候选资格重算不符")
        if qualifies:
            eligible.append(c)
    usable={zone:b for zone,b in blocks.items() if b.get("统计准入")=="1"}
    greedy,proposed,would,extra,eta,reason=predict(eligible,usable)
    expected=(int(greedy is not None),greedy or 0,proposed or 0,int(would),extra,eta)
    actual=tuple(int(decision[k]) for k in ("存在Greedy","GreedyZone","建议Zone","会改选",
                                           "建议额外有效搬运字节","预测达阈值纳秒"))
    require(actual==expected,"Greedy或紧迫度建议重算不符")
    require(int(decision["界限纳秒"])==10*SECOND and int(decision["预算字节"])==EXTRA,
            "界限或预算发生变化")
    switched=decision["实际改选"]=="1"
    require(int(decision["实际Zone"])==(proposed if switched else greedy or 0),"实际区不符合建议或Greedy回退")
    require(int(decision["实际额外有效搬运字节"])==(extra if switched else 0),"实际额外有效量不符")
    if switched:
        require(would and decision["原因"]==reason and decision["帧可用"]=="1","未满足改选准入却实际改选")
    return switched


def analyze(run):
    require(run.name=="v1-execution-smoke-direct-20261014","不是本轮固定功能测试目录")
    for name in ("mkfs.exit","run.exit","verify.exit","runner.exit"):
        require((run/name).read_text().strip()=="0",name+"没有成功")
    require((run/"identity.initial.sha256").read_bytes()==(run/"identity.final.sha256").read_bytes(),"运行中软件身份变化")
    guest=Path("/home/femu/rrzc-v1-execution-results")/run.name
    for line in (run/"logs.sha256").read_text().splitlines():
        digest,path=line.split(maxsplit=1)
        local=run/Path(path).relative_to(guest)
        require(hashlib.sha256(local.read_bytes()).hexdigest()==digest,"原始文件哈希不符："+str(local))
    config=rows(run/"config.csv")
    require(config==[{"试验用途":"功能联调","RR阈值":"4096","GC启动比例":"20","回收预算字节":"201326592",
        "紧迫界限秒数":"10","额外有效搬运预算字节":"16777216","计划真实读次数":"4032","填充区数":"24"}],"固定功能配置不符")
    initial={int(r["组号"]):r for r in rows(run/"initial.csv")}
    require(set(initial)=={0,1} and initial[0]["源Zone"]!=initial[1]["源Zone"],"初始冷热区记录不完整")
    for i,r in initial.items():
        require(int(r["有效字节"])==(32 if i else 16)*(1<<20) and r["初始RC"]=="0" and r["空闲比例"]=="10","初始有效量或空间条件不符")
    reads=rows(run/"reads.csv")
    require(len(reads)==4032 and all(int(r["序号"])==i and r["字节"]=="4096" and
        int(r["API耗时纳秒"])>=0 for i,r in enumerate(reads)),"实际读取数量、顺序或字节不符")
    require(all(int(b["开始纳秒"])>=int(a["开始纳秒"]) for a,b in zip(reads,reads[1:])) and
        int(reads[-1]["开始纳秒"])<2*SECOND,"读取时间顺序或发送时限不符")
    recent={(r["阶段"],int(r["组号"])):r for r in rows(run/"recent.csv")}
    require(len(recent)==6,"三个阶段的逐块记录不完整")
    for i in (0,1):
        before,ready,after=(recent[(phase,i)] for phase in ("热读前","释放前","首次维护后"))
        for key in ("设备会话高位","设备会话低位","Zone"):
            require(before[key]==ready[key]==after[key],"块观测跨会话或跨区")
        require(before["Zone"]==initial[i]["源Zone"] and before["RC"]=="0","初始源区或读数不符")
        require(before["设备代际"]==ready["设备代际"] and before["块编号"]==ready["块编号"],"真实增量不是同块同代际")
        require(ready["窗口标志"]=="7" and int(ready["快照纳秒"])-int(ready["代际起点纳秒"])>=30*SECOND,"窗口未完整预热")
        require(all(int(ready[k])==(4032 if i else 0) for k in ("RC","十秒真实增量","三十秒真实增量")),"真实同块读取增量不符")
        require(all(after[k]=="0" for k in ("RC","十秒真实增量","三十秒真实增量")),"维护后源块读数不为零")
        require(int(after["设备代际"])==int(ready["设备代际"])+(1 if i else 0),"设备Reset代际不符")
    completed={int(r["组号"]):r for r in rows(run/"completion.csv")}
    require(set(completed)=={0,1},"完成映射记录缺失")
    for i,r in completed.items():
        require(r["源Zone"]==initial[i]["源Zone"] and r["有效字节"]==initial[i]["有效字节"],"完成源区或长度不符")
        require((r["当前Zone"]!=r["源Zone"])==bool(i),"热区未迁移或冷区被提前迁移")
        require(int(r["源主机代际"])==int(initial[i]["主机代际"])+i and
            int(r["源设备代际"])==int(initial[i]["设备代际"])+i,"主机或设备Reset代际不符")
    lines=(run/"zenfs.run.log").read_text().splitlines(); text="\n".join(lines)
    require("V1真实：已启用" in text and "[ERROR]" not in text,"真实入口未启用或出现维护错误")
    require(set(re.findall(r"GC 检查：.*启动水平=(\d+)%",text))=={"20"},"未确认原GC20%")
    pending=[]; blocks={}; decisions=[]; chosen=[]; results=[]; last=None
    for number,line in enumerate(lines,1):
        if "简单双因素候选：" in line:
            pending.append(fields(line,"简单双因素候选："))
        if "V1影子候选：" in line:
            b=fields(line,"V1影子候选：")
            if "当前RC" in b:
                blocks[int(b["Zone"])]=b
        if "V1真实决策：" in line:
            d=fields(line,"V1真实决策："); switched=decision_check(pending,blocks,d)
            d["行号"]=number; d["独立重算改选"]=switched
            if switched:
                require(d["GreedyZone"]==initial[0]["源Zone"] and d["实际Zone"]==initial[1]["源Zone"],"不是预算合格的冷热改选")
                hot=blocks[int(d["实际Zone"])]
                require(hot["当前RC"]=="4032" and hot["十秒真实增量"]=="4032" and hot["三十秒真实增量"]=="4032","选区时真实读增量已变化")
                chosen.append(d)
            decisions.append(d); pending=[]; blocks={}; last=d
        if "简单双因素实际集合：" in line and last and last["存在Greedy"]=="1":
            a=fields(line,"简单双因素实际集合：")
            require(a["实际选中"]=="1" and a["Zone"]==last["实际Zone"] and a["实际使用等权"]=="0","最终单区集合未使用实际决策")
        if "V1真实维护结果：" in line:
            result=fields(line,"V1真实维护结果："); result["行号"]=number; results.append(result)
    require(len(chosen)==1 and len(results)==1,"不是一次真实改选和一次维护结果")
    d,r=chosen[0],results[0]
    require(r["轮次"]==d["轮次"] and r["实际Zone"]==d["实际Zone"] and r["实际改选"]=="1" and
        r["选择时额外有效搬运字节"]==str(EXTRA) and r["本轮迁移字节"]==str(32<<20) and
        r["本轮重置区数"]=="1" and r["迁移成功"]=="1","真实维护结果与选择不一致")
    stats=[tuple(map(int,m.groups())) for line in lines if (m:=ZC_STATS.search(line))]
    require(stats==[(1,32<<20,1)] and "RR 开始：" not in text and "RR 统计：" not in text,"维护次数或搬运量不符")
    copy=[n for n,line in enumerate(lines,1) if "V1后续迁移复制：" in line and
        f"源Zone={d['实际Zone']}，" in line and "复制成功=1" in line]
    commit=[n for n,line in enumerate(lines,1) if "V1后续映射提交：" in line and
        "文件名="+initial[1]["文件"]+"，" in line and "成功=1" in line]
    resets=[n for n,line in enumerate(lines,1) if f"Zone 重置诊断：Zone={d['实际Zone']}，状态=成功，" in line]
    require(len(copy)==len(commit)==len(resets)==1 and d["行号"]<copy[0]<commit[0]<r["行号"] and
        copy[0]<resets[0]<r["行号"],"复制、映射提交或Reset证据缺失/顺序不符")
    require("状态=失败" not in text,"有失败的Reset或维护诊断")
    require(rows(run/"verify.csv")==[{"完整有效数据字节":str(48<<20),"填充文件数":"24","每填充文件抽检首尾字节":"4096"}],"独立数据校验不符")
    verify=(run/"zenfs.verify.log").read_text()
    require("GC 检查：" not in verify and "RR 开始：" not in verify and "V1真实决策：" not in verify and
        "[ERROR]" not in verify,"只读阶段运行了维护或出现错误")
    zones=json.loads((run/"zones.final.json").read_text())["zone_list"]
    source=int(d["实际Zone"]); z=next(v for v in zones if int(v["slba"])*512==source)
    require(z["state"]=="EMPTY" and z["wp"]==z["slba"],"设备源Zone未真正Reset为空")
    return {"核验通过":True,"试验用途":"一次真实改选功能联调，不是收益对照",
        "真实改选":d,"实际维护结果":r,"影子核心重算轮数":len(decisions),
        "实际ZC次数":1,"实际RR次数":0,"ZC迁移MiB":32,"额外有效量预算差MiB":16,
        "复制证据行号":copy[0],"映射提交证据行号":commit[0],"源Reset证据行号":resets[0],
        "完整有效数据校验MiB":48,"原始读取次数":len(reads),"已证明净收益":False,
        "说明":"功能读输入在释放前结束，未验证继续热读的RR反事实；RR为零不能视为避免RR。额外16MiB为相对同轮Greedy的快照差，不是同负载净搬运差。"}


if __name__=="__main__":
    require(len(sys.argv)==2,"需要本轮原始结果目录")
    print(json.dumps(analyze(Path(sys.argv[1])),ensure_ascii=False,indent=2))
