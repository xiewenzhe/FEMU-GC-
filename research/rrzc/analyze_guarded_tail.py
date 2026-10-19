#!/usr/bin/env python3
"""核验独立持续读尾部专项；真实未知回退不等于已证明净收益。"""
import hashlib
import json
from pathlib import Path
import re
import sys
from analyze_stream import rows
from analyze_periodic import latency
from analyze_tail_pair import check_reads
from analyze_tail_stream import ledger
from analyze_v1_shadow_stream import fields,require,SECOND
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from guarded_rounds import parse

MIB=1<<20


def check_ongoing(records):
    require(len(records)==1120,"固定持续读次数改变")
    previous=0
    for i,r in enumerate(records):
        begin,duration=int(r["开始纳秒"]),int(r["API耗时纳秒"])
        due=i*SECOND//32
        require(r["序号"]==str(i) and int(r["计划纳秒"])==due and r["字节"]=="4096" and
            begin>=max(due,previous) and duration>=0,"持续读计划、时序或API计时不符")
        previous=begin+duration
    require(previous<36*SECOND,"固定持续读窗口超过时限")
    return hashlib.sha256(json.dumps([(r["序号"],r["计划纳秒"],r["字节"]) for r in records],
        ensure_ascii=False).encode()).hexdigest()


def check_fallback(decisions,before):
    suggested=[d for d in decisions if d["原会改选"]=="1"]
    require(len(suggested)==1,"未形成唯一近期建议，保留真实结果，不当作否决路径已通过")
    d=suggested[0]
    require(d["GreedyZone"]==before["冷Zone"] and d["短期建议Zone"]==before["热Zone"] and
        d["实际Zone"]==before["冷Zone"] and d["实际改选"]=="0" and
        d["原额外有效字节"]==str(16*MIB) and int(d["预测达阈值纳秒"])<=10*SECOND,
        "建议、预算或实际回退不符")
    require(d["尾部分类"]=="未知回退" and d["尾部允许"]==d["复核后可改选"]=="0",
        "尾部边界没有回退或误称确定压力拦截")
    require("Greedy稀疏头边界未知" in d["尾部原因"],"未知原因不是预注册的物理边界")
    require(all(d["实际改选"]=="0" for d in decisions),"出现计划外实际改选")
    return d


def analyze(run):
    config=rows(run/"config.csv");require(len(config)==1,"配置不唯一")
    strategy=config[0]["策略"];require(strategy in ("greedy","guarded"),"未知策略")
    require(run.name==f"guarded-tail-{strategy}-20261014","非独立尾部目录")
    require(config==[{"策略":strategy,"固定观察秒数":"35","RR阈值":"4096","GC启动比例":"20",
        "回收预算字节":"201326592","种子有效字节":"251658240","种子真实读取次数":"4096",
        "热区预读次数":"3904","填充区数":"24","持续读次数":"1120","持续读速率":"32","相位等待秒数":"5"}],
        "预注册输入或原维护参数改变")
    for name in ("mkfs.exit","run.exit","verify.exit","runner.exit"):
        require((run/name).read_text().strip()=="0",name+"未成功")
    require((run/"identity.initial.sha256").read_bytes()==(run/"identity.final.sha256").read_bytes(),
        "运行期间源码或库改变")
    guest=Path("/home/femu/rrzc-tail-pair-results")/run.name
    for line in (run/"logs.sha256").read_text().splitlines():
        digest,path=line.split(maxsplit=1)
        require(hashlib.sha256((run/Path(path).relative_to(guest)).read_bytes()).hexdigest()==digest,"原始文件哈希不符")
    identity={name:digest for digest,name in (s.split() for s in (run/"identity.initial.sha256").read_text().splitlines())}
    software={Path(name).name if name.startswith("/home/femu/") else name:digest for name,digest in identity.items()}
    require(software["guarded_tail_test.cc"]=="88e588b2a236a9302a1addd5edc5b54e6020002d30ad09bfc37f464c64906c94",
        "运行后修改了预注册测试源")
    check_reads(rows(run/"seed.reads.csv"),4096);check_reads(rows(run/"hot.reads.csv"),3904)
    ongoing=rows(run/"ongoing.reads.csv");trace=check_ongoing(ongoing)
    before,first,final=rows(run/"states.csv")
    require([s["阶段"] for s in (before,first,final)]==["释放后起点","首次迁移后","固定窗口结束"],
        "阶段快照不完整")
    require(before["经过纳秒"]=="0" and 35*SECOND<=int(final["经过纳秒"])<36*SECOND,"固定窗口改变")
    for s in (before,first,final):
        require(s["预留存在"]=="1" and int(s["种子长度"])==240*MIB and int(s["冷长度"])==16*MIB and
            int(s["热长度"])==32*MIB and int(s["有效字节"])==6432*MIB,"有效布局不符")
    require(int(before["预留尾部"])==16*MIB and int(before["预留有效量"])==240*MIB and
        before["预留Zone"]==before["种子Zone"],"真实RR没有形成预注册尾部")
    require(first["冷Zone"]!=before["冷Zone"] and first["热Zone"]==before["热Zone"],
        "首轮没有先维护Greedy冷区")
    require(before["种子Zone"]==first["种子Zone"]==final["种子Zone"],"种子出现计划外再次迁移")
    recent=rows(run/"recent.csv")
    staged={(r["阶段"],r["组号"]):r for r in recent if r["阶段"]!="持续观察"}
    require(len(staged)==6 and sum(r["阶段"]=="持续观察" for r in recent)==140,"逐块轨迹不完整")
    for i in (0,1):
        a,b,c=(staged[(p,str(i))] for p in ("热读前","释放前","观察结束"))
        for k in ("设备会话高位","设备会话低位","Zone"):
            require(a[k]==b[k]==c[k],"采样跨会话或跨区")
        require(a["RC"]=="0" and a["设备代际"]==b["设备代际"] and a["块编号"]==b["块编号"],
            "预读跨物理块身份或Reset")
        require(b["窗口标志"]=="7" and all(int(b[k])==(3904 if i else 0) for k in
            ("RC","十秒真实增量","三十秒真实增量")),"准备真实读增量不符")
        require(int(c["设备代际"])==int(b["设备代际"])+1 and c["RC"]=="0","候选源Reset及清零不符")
    lines=(run/"zenfs.run.log").read_text().splitlines();text="\n".join(lines)
    require("[ERROR]" not in text and "状态=失败" not in text,"维护存在失败")
    require(set(re.findall(r"GC 检查：.*启动水平=(\d+)%",text))=={"20"},"GC20%未确认")
    require(text.count("独立尾部约束：已启用")==1 and
        f"独立尾部约束：已启用，真实执行={int(strategy=='guarded')}，" in text,"独立入口模式不符")
    require(not any(s in text for s in ("V1真实：已启用","V1影子：已启用","短期影子：已启用",
        "成本观察：已启用","ZC实验单次选区：已启用")),"混用旧入口")
    require("独立尾部结束：固定35秒与1120次持续读完成" in text,"固定业务未完成")
    decisions,files,maintenance=parse(lines,strategy);fallback=check_fallback(decisions,before)
    zc=[tuple(map(int,m.groups())) for line in lines if (m:=ZC_STATS.search(line))]
    rr=[tuple(map(int,m.groups())) for line in lines if (m:=RR_STATS.search(line))]
    require(zc==[(1,16*MIB,1)] and rr==[(1,240*MIB,1,240*MIB),(2,32*MIB,1,272*MIB)],
        "准备／后续RR或ZC真实总账不符合功能输入，保留原结果")
    require(len(maintenance)==1 and maintenance[0]["本轮迁移字节"]==str(16*MIB) and text.count("RR 完成：")==2,
        "决策后维护或RR完成未闭合")
    pool=[fields(line,"独立约束预留：") for line in lines if "独立约束预留：" in line]
    p=next(r for r in pool if r["轮次"]==fallback["轮次"])
    require(p["可知"]==p["存在"]=="1" and int(p["剩余字节"])==16*MIB and int(p["有效字节"])==240*MIB,
        "否决时真实尾部不符")
    candidates=[fields(line,"V1影子候选：") for line in lines if "V1影子候选：" in line]
    current=next(r for r in candidates if r["轮次"]==fallback["轮次"] and r["Zone"]==fallback["短期建议Zone"])
    growth=[fields(line,"V1增长候选：") for line in lines if "V1增长候选：" in line]
    growing=next(r for r in growth if r["轮次"]==fallback["轮次"] and r["Zone"]==current["Zone"])
    require(3904<int(current["当前RC"])<4096 and growing["真实增量"]!="0",
        "否决前不是未跨阈值且仍在读取的同块")
    results=ledger(lines)
    require(sum(int(c["有效字节"]) for c in results["实际复制"])==288*MIB and
        len(results["实际复制"])==3 and results["重复文件复制字节"]==0,"真实复制总账不符")
    require(not results["预留替换"] and not results["预留源RR封闭"] and
        len(results["自然写满后取得新预留"])==1,"不是预注册的无提前Finish路径")
    require(rows(run/"tail.verify.csv")==[{"完整有效数据字节":str(288*MIB),"填充文件数":"24",
        "每填充文件抽检首尾字节":"4096"}],"完整数据校验未完成")
    verify=(run/"zenfs.verify.log").read_text()
    require(not any(s in verify for s in ("GC 检查：","RR 开始：","独立约束决策：","[ERROR]")),
        "只读校验引入维护或错误")
    return dict(核验通过=True,来源=str(run),策略=strategy,配置=config[0],软件身份=software,
        持续读计划哈希=trace,首次选区前布局=before,首次维护后布局=first,终态布局=final,
        决策=decisions,真实未知回退=fallback,否决时预留=p,紧迫物理块=current,近期增长证据=growing,候选文件=files,
        共同准备RR次数=1,共同准备RR迁移MiB=240,后续RR次数=1,后续RR迁移MiB=32,
        ZC次数=1,ZC迁移MiB=16,全程迁移MiB=288,重复文件复制MiB=0,
        持续读API耗时=latency([int(r["API耗时纳秒"]) for r in ongoing]),尾部事件账目=results,
        已证明净收益=False,说明="固定功能专项：当前护栏真实回退；没有取消护栏的同输入对照，不证明避免Finish或净收益。")


def compare(a,b):
    require(a["核验通过"] and b["核验通过"] and a["策略"]=="greedy" and b["策略"]=="guarded","非有效配对")
    for key in ("软件身份","持续读计划哈希","首次选区前布局"):
        require(a[key]==b[key],"两组初始条件不同："+key)
    require({k:v for k,v in a["配置"].items() if k!="策略"}=={k:v for k,v in b["配置"].items() if k!="策略"},
        "两组固定参数不同")
    return dict(核验通过=True,Greedy=a,独立约束=b,净迁移变化MiB=b["全程迁移MiB"]-a["全程迁移MiB"],
        终态布局一致=(a["终态布局"]|{"经过纳秒":"忽略"})==(b["终态布局"]|{"经过纳秒":"忽略"}),
        说明="同输入真实验证未知回退及维护账目，不把功能输入或此前不同读计划当净收益对照。")


if __name__=="__main__":
    require(len(sys.argv) in (2,3),"输入单组或Greedy／独立执行两组目录")
    a=analyze(Path(sys.argv[1]))
    print(json.dumps(a if len(sys.argv)==2 else compare(a,analyze(Path(sys.argv[2]))),ensure_ascii=False,indent=2))
