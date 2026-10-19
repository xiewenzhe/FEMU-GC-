#!/usr/bin/env python3
"""独立核验固定16MiB尾部小配对；空间丢弃与净搬运分别计量。"""
import hashlib
import json
from pathlib import Path
import re
import sys
from collections import Counter
from analyze_stream import rows
from analyze_tail_stream import ledger
from analyze_v1_shadow_stream import fields, require, SECOND
from analyze_v1_execution_smoke import decision_check
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS

MIB=1<<20


def check_reads(records,count):
    require(len(records)==count,"固定预读数量不符")
    for i,r in enumerate(records):
        require(r["序号"]==str(i) and int(r["计划纳秒"])==i*SECOND//4096 and r["字节"]=="4096" and
            int(r["开始纳秒"])>=int(r["计划纳秒"]) and int(r["API耗时纳秒"])>=0,"固定预读顺序或计时不符")
    require(int(records[-1]["开始纳秒"])+int(records[-1]["API耗时纳秒"])<2*SECOND,"预读发送超时")
    require(all(int(b["开始纳秒"])>=int(a["开始纳秒"]) for a,b in zip(records,records[1:])),"读取时序倒退")


def choices(lines,strategy):
    simple=[]; blocks={}; last=None; actual=[]; decisions=[]
    marker="V1真实决策：" if strategy=="v1" else "V1影子决策："
    for n,line in enumerate(lines,1):
        if "简单双因素候选：" in line: simple.append(fields(line,"简单双因素候选："))
        if "V1影子候选：" in line:
            b=fields(line,"V1影子候选：")
            if "当前RC" in b: blocks[int(b["Zone"])]=b
        if marker in line:
            d=fields(line,marker)
            if strategy=="greedy":
                d=dict(d,GreedyZone=d["实际Greedy"],建议Zone=d["影子建议"],实际Zone=d["实际Greedy"],
                       建议额外有效搬运字节=d["额外有效搬运字节"],实际额外有效搬运字节="0",实际改选="0")
            switched=decision_check(simple,blocks,d)
            d["独立核验实际改选"]=switched; d["行号"]=n
            decisions.append(d); last=d; simple=[]; blocks={}
        if "简单双因素实际集合：" in line:
            c=fields(line,"简单双因素实际集合：")
            if c["实际选中"]=="1":
                require(last and last["存在Greedy"]=="1" and c["Zone"]==last["实际Zone"] and
                    c["实际使用等权"]=="0","实际集合与重算选择不符")
                actual.append(dict(c,行号=n))
    require(decisions and actual,"没有可核验的真实选择")
    return decisions,actual


def analyze(run):
    config=rows(run/"config.csv"); require(len(config)==1,"配置不唯一")
    strategy=config[0]["策略"]; require(strategy in ("greedy","v1"),"未知策略")
    require(run.name==f"tail-pair-{strategy}-v3-20261014","结果目录与策略不符")
    require(config==[{"策略":strategy,"固定观察秒数":"35","RR阈值":"4096","GC启动比例":"20",
        "回收预算字节":"201326592","种子有效字节":"251658240","种子真实读取次数":"4096",
        "热区真实读取次数":"4032","填充区数":"24","后续业务读写次数":"0"}],"预先固定配置改变")
    for name in ("mkfs.exit","run.exit","verify.exit","runner.exit"):
        require((run/name).read_text().strip()=="0",name+"未成功")
    require((run/"identity.initial.sha256").read_bytes()==(run/"identity.final.sha256").read_bytes(),"运行中软件身份改变")
    guest=Path("/home/femu/rrzc-tail-pair-results")/run.name
    for line in (run/"logs.sha256").read_text().splitlines():
        digest,path=line.split(maxsplit=1)
        require(hashlib.sha256((run/Path(path).relative_to(guest)).read_bytes()).hexdigest()==digest,"原始文件哈希不符")
    check_reads(rows(run/"seed.reads.csv"),4096); check_reads(rows(run/"hot.reads.csv"),4032)
    before,first,final=rows(run/"states.csv")
    require([s["阶段"] for s in (before,first,final)]==["释放后起点","首次迁移后","固定窗口结束"],"阶段记录不符")
    require(before["经过纳秒"]=="0" and int(final["经过纳秒"])>=35*SECOND,"固定观察窗口不符")
    for s in (before,first,final):
        require(s["预留存在"]=="1" and int(s["种子长度"])==240*MIB and int(s["冷长度"])==16*MIB and
            int(s["热长度"])==32*MIB and int(s["有效字节"])==6432*MIB,"有效布局或阶段有效量不符")
    require(int(before["预留尾部"])==16*MIB and int(before["预留有效量"])==240*MIB and
        before["预留Zone"]==before["种子Zone"] and before["冷Zone"]!=before["热Zone"],"首次选区前尾部条件不符")
    require((first["冷Zone"]!=before["冷Zone"])==(strategy=="greedy") and
        (first["热Zone"]!=before["热Zone"])==(strategy=="v1"),"首次观察映射不符合首轮选区")
    require(before["种子Zone"]==first["种子Zone"]==final["种子Zone"],"种子出现计划外后续迁移")
    recent={(r["阶段"],r["组号"]):r for r in rows(run/"recent.csv")}; require(len(recent)==6,"逐块阶段记录缺失")
    for i in (0,1):
        previous,ready,after=(recent[(phase,str(i))] for phase in ("热读前","释放前","观察结束"))
        for k in ("设备会话高位","设备会话低位","Zone"):
            require(previous[k]==ready[k]==after[k],"逐块统计跨会话或跨区")
        require(previous["设备代际"]==ready["设备代际"] and previous["块编号"]==ready["块编号"] and previous["RC"]=="0", "预读不是同块同代际")
        require(ready["窗口标志"]=="7" and all(int(ready[k])==(4032 if i else 0) for k in ("RC","十秒真实增量","三十秒真实增量")),"逐块真实增量不符")
        require(int(after["设备代际"])==int(ready["设备代际"])+1 and after["RC"]=="0","候选源Reset代际或RC不符")
    lines=(run/"zenfs.run.log").read_text().splitlines(); text="\n".join(lines)
    require("[ERROR]" not in text and "状态=失败" not in text,"存在维护错误")
    require(set(re.findall(r"GC 检查：.*启动水平=(\d+)%",text))=={"20"},"原GC20%未确认")
    require("尾部配对结束：固定35秒观察完成，无追加业务读写" in text,"固定观察未结束")
    require(not any(marker in text for marker in ("短期影子：已启用","V1增长准入：已启用","成本观察：已启用","ZC实验单次选区：已启用")),"误启用其他机制")
    decisions,actual=choices(lines,strategy)
    require(len(actual)==2,"不是两次候选ZC，保留真实结果，不隐藏额外维护")
    expected=[before["热Zone"],before["冷Zone"]] if strategy=="v1" else [before["冷Zone"],before["热Zone"]]
    require([r["Zone"] for r in actual]==expected,"没有形成预定首轮选区分支对照")
    require(sum(d["独立核验实际改选"] for d in decisions)==int(strategy=="v1"),"真实改选次数不符")
    zc=[tuple(map(int,m.groups())) for line in lines if (m:=ZC_STATS.search(line))]
    rr=[tuple(map(int,m.groups())) for line in lines if (m:=RR_STATS.search(line))]
    require(text.count("RR 完成：")==len(rr),"RR完成与统计数量不一致")
    require(zc==[(1,32*MIB if strategy=="v1" else 16*MIB,1),
                 (2,16*MIB if strategy=="v1" else 32*MIB,1)] and rr==[(1,240*MIB,1,240*MIB)],"共同准备RR或固定窗口ZC账目不符")
    result=ledger(lines)
    require(sum(int(c["有效字节"]) for c in result["实际复制"])==288*MIB and
        len(result["实际复制"])==3 and result["重复文件复制字节"]==0,"实际复制与维护总账不符")
    require(Counter(c["维护类型"] for c in result["实际复制"])=={"RR":1,"ZC":2},"复制维护类型归因不符")
    require(len(result["预留替换"])==int(strategy=="v1"),"实际提前Finish分支未确认")
    require(len(result["自然写满后取得新预留"])==int(strategy=="greedy"),"自然写满分支未确认")
    require(not result["预留源RR封闭"],"出现非计划预留源RR封闭")
    require(result["可确认丢弃尾部字节"]==(16*MIB if strategy=="v1" else 0),"丢弃尾部账目不符")
    if result["预留替换"]:
        e=result["预留替换"][0]
        require(e["当时已追踪有效字节"]==240*MIB and e["首次替换后复制"]["维护类型"]=="ZC" and
            int(e["首次替换后复制"]["有效字节"])==32*MIB and e["旧区有效文件后续复制字节"]==0,"Finish或其后复制事实不符")
    require(rows(run/"tail.verify.csv")==[{"完整有效数据字节":str(288*MIB),"填充文件数":"24","每填充文件抽检首尾字节":"4096"}],"完整有效数据校验不符")
    verify=(run/"zenfs.verify.log").read_text()
    require(not any(s in verify for s in ("GC 检查：","RR 开始：","V1真实决策：","V1影子决策：","[ERROR]")),"只读校验引入维护或错误")
    identity={name:digest for digest,name in (s.split() for s in (run/"identity.initial.sha256").read_text().splitlines())}
    software={Path(name).name if name.startswith("/home/femu/") else name:digest for name,digest in identity.items()}
    return dict(核验通过=True,来源=str(run),策略=strategy,固定观察秒数=35,软件身份=software,
        首次选区前布局=before,首次维护后布局=first,终态布局=final,决策=decisions,实际ZC集合=actual,
        共同准备RR次数=1,共同准备RR迁移MiB=240,ZC次数=2,ZC迁移MiB=48,
        全程迁移MiB=288,重复文件复制MiB=0,尾部事件账目=result,
        已证明RR或净迁移收益=False,说明="只有目标尾部分支的受控功能对照，预读后有意停读，预测不作为未来RR证据。")


def compare(g,v):
    require(g["核验通过"] and v["核验通过"] and g["策略"]=="greedy" and v["策略"]=="v1","非有效配对")
    for key in ("固定观察秒数","软件身份","首次选区前布局"):
        require(g[key]==v[key],"两组初始条件或软件不同："+key)
    free_loss=int(g["终态布局"]["空闲字节"])-int(v["终态布局"]["空闲字节"])
    invalid_added=int(v["终态布局"]["满区可回收字节"])-int(g["终态布局"]["满区可回收字节"])
    require(free_loss==invalid_added==16*MIB,"终态空闲损失或额外满区无效量不等于实测丢弃尾部")
    return dict(核验通过=True,Greedy=g,V1=v,净迁移增加MiB=v["全程迁移MiB"]-g["全程迁移MiB"],
        空闲损失MiB=free_loss/MIB,新增满区无效MiB=invalid_added/MIB,
        结论="本条件下热区改选导致提前Finish并丢弃16MiB，但35秒内没有重复复制或净迁移增加；空间损失不能当搬运量。")


if __name__=="__main__":
    require(len(sys.argv) in (2,3),"提供单组目录核验，或Greedy/V1两个目录比较")
    result=analyze(Path(sys.argv[1]))
    if len(sys.argv)==3: result=compare(result,analyze(Path(sys.argv[2])))
    print(json.dumps(result,ensure_ascii=False,indent=2))
