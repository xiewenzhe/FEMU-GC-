#!/usr/bin/env python3
"""核验固定计划受控配对；联合报告RR、ZC、净搬运及实际业务API耗时。"""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
from collections import Counter
from analyze_v1_shadow_stream import fields, predict, require, BUDGET, EXTRA, SECOND
from analyze_v1_execution_smoke import decision_check
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def expected_plan(profile):
    require(profile in ("continuous","stop","retire"),"场景未知")
    items=[(i*62500000,0,i) for i in range(768 if profile=="continuous" else 8)]
    if profile=="retire": items.append((5*SECOND,4,0))
    items.append((32*SECOND,1,0))
    items.extend((32*SECOND+i*125000000,2,i) for i in range(8))
    items.append((33*SECOND,3,0))
    return [{"顺序":str(i),"计划纳秒":str(t),"类型":str(kind),"序号":str(n)}
            for i,(t,kind,n) in enumerate(sorted(items))]


def business_latency(ops,kind):
    samples=sorted(int(r["API耗时纳秒"])/1000 for r in ops if int(r["类型"])==kind)
    require(samples,"缺少实测API样本")
    def quantile(p):
        pos=(len(samples)-1)*p; low=int(pos); high=min(low+1,len(samples)-1)
        return samples[low]+(samples[high]-samples[low])*(pos-low)
    return {"请求数":len(samples),"平均微秒":sum(samples)/len(samples),
            "P95微秒":quantile(.95),"P99微秒":quantile(.99)}


def analyze_run(run, decision_validator=decision_check):
    config=rows(run/"config.csv")
    require(len(config)==1,"配置记录不唯一")
    profile,strategy=config[0]["场景"],config[0]["策略"]
    require(strategy in ("greedy","v1"),"策略未知")
    require(run.name==f"v1-pair-{profile}-{strategy}-20261014","目录与策略场景不符")
    require(config==[{"策略":strategy,"场景":profile,"业务观察秒数":"60","RR阈值":"4096","GC启动比例":"20",
        "回收预算字节":"201326592","紧迫界限秒数":"10","额外有效搬运预算字节":"16777216","预读次数":"4032","填充区数":"24"}],"固定配置发生改变")
    for name in ("mkfs.exit","run.exit","verify.exit","runner.exit"):
        require((run/name).read_text().strip()=="0",name+"未成功")
    require((run/"identity.initial.sha256").read_bytes()==(run/"identity.final.sha256").read_bytes(),"运行中软件身份改变")
    guest=Path("/home/femu/rrzc-v1-pair-results")/run.name
    for line in (run/"logs.sha256").read_text().splitlines():
        digest,filename=line.split(maxsplit=1)
        local=run/Path(filename).relative_to(guest)
        require(hashlib.sha256(local.read_bytes()).hexdigest()==digest,"原始文件哈希不符："+str(local))
    scheduled=rows(run/"plan.csv"); require(scheduled==expected_plan(profile),"发送计划不符合预先固定内容")
    ops=rows(run/"operations.csv"); require(len(ops)==len(scheduled),"实际请求数量不符")
    for actual,planned in zip(ops,scheduled):
        require(all(actual[k]==v for k,v in planned.items()),"实际请求次序或参数不同于固定计划")
        kind=int(actual["类型"]); expected_bytes=4096 if kind==0 else (1<<20) if kind==2 else 0
        start,duration,late=(int(actual[k]) for k in ("开始纳秒","API耗时纳秒","迟到纳秒"))
        require(actual["成功"]=="1" and int(actual["字节"])==expected_bytes and duration>=0 and
                start>=0 and late==max(0,start-int(actual["计划纳秒"])),"业务请求失败或计时不符")
    require(all(int(b["开始纳秒"])>=int(a["开始纳秒"]) for a,b in zip(ops,ops[1:])),"API时序倒退")
    initial=rows(run/"initial.csv")
    require(len(initial)==2 and len({r["源Zone"] for r in initial})==2,"初始候选布局不完整")
    for i,r in enumerate(initial):
        require(r["组号"]==str(i) and int(r["有效字节"])==(32 if i else 16)*(1<<20) and
                r["空闲比例"]=="10" and r["初始RC"]=="0","初始布局与固定条件不符")
    prep=rows(run/"reads.csv")
    require(len(prep)==4032 and all(r["序号"]==str(i) and r["字节"]=="4096" for i,r in enumerate(prep)),"真实预读计划未完成")
    recent=rows(run/"recent.csv")
    require(len(recent)==6,"逐块阶段记录缺失")
    for i in (0,1):
        before=next(r for r in recent if r["阶段"]=="热读前" and r["组号"]==str(i))
        ready=next(r for r in recent if r["阶段"]=="释放前" and r["组号"]==str(i))
        require(all(before[k]==ready[k] for k in ("Zone","设备代际","块编号","设备会话高位","设备会话低位")),"预读不是同块同代际")
        require(ready["Zone"]==initial[i]["源Zone"] and ready["窗口标志"]=="7" and
            all(int(ready[k])==(4032 if i else 0) for k in ("RC","十秒真实增量","三十秒真实增量")),"预读真实块增量不符")
    lines=(run/"zenfs.run.log").read_text().splitlines(); text="\n".join(lines)
    require("[ERROR]" not in text and "状态=失败" not in text,"出现维护错误")
    require(set(re.findall(r"GC 检查：.*启动水平=(\d+)%",text))=={"20"},"原GC20%未确认")
    require(f"启动 Zone 维护线程：GC=1，主机端 RR=1" in text,"RR/ZC线程未同时启用")
    require(re.search(r"V1配对业务：固定观察结束，经过纳秒=(\d+)",text) is not None,"业务窗口没有固定结束记录")
    elapsed=int(re.search(r"固定观察结束，经过纳秒=(\d+)",text)[1]); require(elapsed>=60*SECOND,"观察提前结束")
    pending=[]; blocks={}; decisions=[]; last=None; actual_choices=[]
    for number,line in enumerate(lines,1):
        if "简单双因素候选：" in line: pending.append(fields(line,"简单双因素候选："))
        if "V1影子候选：" in line:
            b=fields(line,"V1影子候选：")
            if "当前RC" in b: blocks[int(b["Zone"])]=b
        marker="V1真实决策：" if strategy=="v1" else "V1影子决策："
        if marker in line:
            logged=fields(line,marker)
            if strategy=="greedy":
                d=dict(logged,GreedyZone=logged["实际Greedy"],建议Zone=logged["影子建议"],
                       实际Zone=logged["实际Greedy"],建议额外有效搬运字节=logged["额外有效搬运字节"],实际额外有效搬运字节="0")
            else: d=logged
            switched=decision_validator(pending,blocks,d)
            d["行号"]=number; d["独立核验实际改选"]=switched; decisions.append(d)
            last=d; pending=[]; blocks={}
        if "简单双因素实际集合：" in line and last and last["存在Greedy"]=="1":
            chosen=fields(line,"简单双因素实际集合：")
            require(chosen["实际选中"]=="1" and chosen["Zone"]==last["实际Zone"] and
                chosen["实际使用等权"]=="0","实际迁移集合不符合核验决策")
            actual_choices.append({"Zone":chosen["Zone"],"行号":number})
    require(decisions and actual_choices,"没有真实维护决策")
    zc=[tuple(map(int,m.groups())) for line in lines if (m:=ZC_STATS.search(line))]
    rr=[tuple(map(int,m.groups())) for line in lines if (m:=RR_STATS.search(line))]
    require([r[0] for r in zc]==list(range(1,len(zc)+1)),"ZC轮次不连续")
    require([r[0] for r in rr]==list(range(1,len(rr)+1)),"RR轮次不连续")
    require(all(r[2]==1 for r in zc) and all(r[2]==1 for r in rr),"存在未完成源Reset")
    require(len(re.findall(r"RR 完成：",text))==len(rr),"RR完成记录不匹配")
    copies=[]; commits=[]
    for number,line in enumerate(lines,1):
        if "V1后续迁移复制：" in line:
            c=fields(line,"V1后续迁移复制："); require(c["复制成功"]=="1","有迁移复制失败")
            c["行号"]=number; copies.append(c)
        if "V1后续映射提交：" in line:
            c=fields(line,"V1后续映射提交："); require(c["成功"]=="1","有映射提交失败")
            commits.append(c)
    require(Counter(c["文件编号"] for c in copies)==Counter(c["文件编号"] for c in commits),"复制未对应成功映射提交")
    zc_bytes=sum(r[1] for r in zc); rr_bytes=sum(r[1] for r in rr)
    require(sum(int(c["有效字节"]) for c in copies)==zc_bytes+rr_bytes,"实际复制总量与RR/ZC统计不符")
    expected_sizes={r["文件"]:int(r["有效字节"]) for r in initial}
    counts=Counter()
    for c in copies:
        require(c["文件名"] in expected_sizes and int(c["有效字节"])==expected_sizes[c["文件名"]],"本轮搬运不再是已知单片段载荷")
        counts[c["文件名"]]+=1
    repeats=sum(max(0,n-1)*expected_sizes[name] for name,n in counts.items())
    verified=(24 if profile=="retire" else 56)<<20
    require(rows(run/"verify.csv")==[{"完整有效数据字节":str(verified),"填充文件数":"24","每填充文件抽检首尾字节":"4096"}],"独立数据校验不符")
    verify=(run/"zenfs.verify.log").read_text()
    require(not any(marker in verify for marker in ("GC 检查：","RR 开始：","V1真实决策：","V1影子决策：","[ERROR]")),"只读验证引入维护或错误")
    identity={name:digest for digest,name in (r.split() for r in (run/"identity.initial.sha256").read_text().splitlines())}
    same_software={name:digest for name,digest in identity.items() if not name.startswith("/home/femu/")}
    for suffix in ("v1_pair_test","v1_pair_test.cc","run_v1_pair_guest.sh"):
        same_software[suffix]=next(digest for name,digest in identity.items() if name.endswith("/"+suffix))
    return {"核验通过":True,"场景":profile,"策略":strategy,"初始布局":initial,
        "计划SHA256":hashlib.sha256((run/"plan.csv").read_bytes()).hexdigest(),"软件身份":same_software,
        "ZC次数":len(zc),"RR次数":len(rr),"ZC迁移MiB":zc_bytes/(1<<20),"RR迁移MiB":rr_bytes/(1<<20),
        "总维护迁移MiB":(zc_bytes+rr_bytes)/(1<<20),"单片段载荷重复搬运MiB":repeats/(1<<20),
        "预留替换次数":text.count("迁移预留替换："),"预留源额外Finish次数":text.count("RR 封闭专用预留源："),
        "业务读API":business_latency(ops,0),"业务写API":business_latency(ops,2),
        "最大业务迟到毫秒":max(int(r["迟到纳秒"]) for r in ops)/1e6,
        "实际改选次数":sum(d["独立核验实际改选"] for d in decisions),"实际ZC集合":actual_choices,
        "决策":decisions,"完整有效数据校验MiB":verified/(1<<20)}


def compare(greedy,v1):
    require(greedy["核验通过"] and v1["核验通过"] and greedy["策略"]=="greedy" and v1["策略"]=="v1","不是有效Greedy/V1配对")
    for key in ("场景","计划SHA256","初始布局","软件身份"):
        require(greedy[key]==v1[key],"配对条件不同："+key)
    return {"核验通过":True,"场景":greedy["场景"],"Greedy":greedy,"V1":v1,
        "RR减少次数":greedy["RR次数"]-v1["RR次数"],
        "ZC增加次数":v1["ZC次数"]-greedy["ZC次数"],
        "总维护迁移变化MiB（V1减Greedy）":v1["总维护迁移MiB"]-greedy["总维护迁移MiB"],
        "说明":"单次受控短配对，不是稳态或显著性结论。API耗时排除预读/填充/校验，8次写及负例8次读样本很少；未来退役仅负载可见。"}


if __name__=="__main__":
    require(len(sys.argv) in (2,3),"提供一个结果目录核验，或按Greedy/V1顺序提供两个目录比较")
    first=analyze_run(Path(sys.argv[1]))
    result=first if len(sys.argv)==2 else compare(first,analyze_run(Path(sys.argv[2])))
    print(json.dumps(result,ensure_ascii=False,indent=2))
