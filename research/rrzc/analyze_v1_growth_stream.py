#!/usr/bin/env python3
"""只读核验原滚动计划的新准入配对；零改选不能用于证明补清成本或收益。"""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from analyze_stream import check_trace
from analyze_periodic import latency
from analyze_v1_shadow_stream import fields, predict, eta, require, SECOND, BUDGET, EXTRA
from analyze_v1_growth_pair import check_gate
from analyze_v1_execution_smoke import decision_check
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import PREFIX, RR_STATS, RR_START
from analyze_v1_trajectory import read_samples


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def qualifies(c):
    v,cap=int(c["有效字节"]),int(c["容量字节"])
    return 0<v<=cap and cap-v>=BUDGET and c["覆盖资格"]=="1" and c["空目标资格"]=="1"


def empty_collection_count(candidates):
    # 沿用原无法满足单区预算时的批量回退，不擅自将有效行为判成错误。
    possible=any(int(c["容量字节"])-int(c["有效字节"])>=BUDGET for c in candidates)
    return 0 if possible else len(candidates)


def same_generation_outcome(lines, start, zone):
    """截至该地址首次成功Reset；不把重用后维护归给当前代际。"""
    for number in range(start+1,len(lines)+1):
        line=lines[number-1]
        if f"Zone 重置诊断：Zone={zone}，状态=成功，" in line:
            return {"事件":"源Reset，未先观察到RR或ZC选择","行号":number}
        match=RR_START.search(line)
        if match and int(match[1])==zone:
            return {"事件":"独立RR开始","行号":number,"当时有效字节":int(match[3])}
        if "简单双因素实际集合：" in line:
            c=fields(line,"简单双因素实际集合：")
            if c.get("Zone")==str(zone) and c["实际选中"]=="1":
                return {"事件":"后续ZC选择","行号":number}
    return {"事件":"窗口内尚未观察到，不能认定不会RR"}


def switch_ledger(decisions, file_rows, copies, retired):
    """只按决策时文件编号记账，不能仅按后来重用的Zone地址推断补清。"""
    result=[]
    for d in decisions:
        if d["实际改选"]!="1": continue
        cold=[f for f in file_rows if f["轮次"]==d["轮次"] and f["Zone"]==d["GreedyZone"]]
        details=[]
        for f in cold:
            moves=[c for c in copies if c["行号"]>d["行号"] and c["文件编号"]==f["文件编号"]]
            deaths=[r for r in retired if r["行号"]>d["行号"] and r["文件编号"]==f["文件编号"]]
            details.append({"文件编号":f["文件编号"],"文件名":f["文件名"],
                "原区有效字节":int(f["区内有效字节"]),"后续复制":moves,"后续最后链接退役":deaths})
        # 全文件复制量可能包括区外片段，多轮被跳过的集合也可能重叠，不能相加当净增量。
        result.append({"轮次":d["轮次"],"GreedyZone":d["GreedyZone"],"实际Zone":d["实际Zone"],
            "选择时额外有效字节":int(d["实际额外有效搬运字节"]),"被跳过文件后续观测":details,
            "说明":"单次观察账目，不是相对Greedy的因果额外成本；最后链接删除不等于无读句柄"})
    return result


def analyze_run(run):
    config=rows(run/"config.csv"); require(len(config)==1,"配置记录不唯一")
    strategy=config[0]["策略"]; require(strategy in ("greedy","v1"),"策略未知")
    require(run.name==f"v1-growth-stream-{strategy}-20261014","不是本轮固定结果目录")
    require(config==[{"策略":strategy,"计划业务秒数":"180","RR阈值":"4096","GC启动比例":"20",
        "回收预算字节":"201326592","紧迫界限秒数":"10","额外有效搬运预算字节":"16777216",
        "近期准入开关":"1","观察采样毫秒":"500","预热秒数":"0"}],"计划参数发生改变")
    for name in ("mkfs.exit","run.exit","verify.exit","runner.exit"):
        require((run/name).read_text().strip()=="0",name+"未成功")
    require((run/"identity.initial.sha256").read_bytes()==(run/"identity.final.sha256").read_bytes(),"运行期间软件改变")
    guest=Path("/home/femu/rrzc-stream-results")/run.name
    for line in (run/"logs.sha256").read_text().splitlines():
        digest,filename=line.split(maxsplit=1)
        local=run/Path(filename).relative_to(guest)
        require(hashlib.sha256(local.read_bytes()).hexdigest()==digest,"原始文件哈希不符："+str(local))
    text=(run/"zenfs.run.log").read_text(); lines=text.splitlines()
    require("[ERROR]" not in text and "状态=失败" not in text,"出现维护错误")
    require(text.count("V1增长准入：已启用")==1,"增长准入未唯一开启")
    require(set(re.findall(r"GC 检查：.*启动水平=(\d+)%",text))=={"20"},"原GC20%未确认")
    operations=rows(run/"operations.csv"); traces=check_trace(operations,180)
    for r in operations:
        require(int(r["延迟纳秒"])>=0 and int(r["排程迟到纳秒"])>=0,"API计时不合法")
    verify=(run/"verify.stdout.log").read_text()
    require("业务完整比对字节=687865856" in verify and "填充文件首4KiB抽检=20" in verify,"独立数据比对不完整")
    verify_log=(run/"zenfs.verify.log").read_text()
    require(not any(s in verify_log for s in ("GC 检查：","RR 开始：","V1真实决策：","V1影子决策：","[ERROR]")),"只读校验引入维护或错误")
    samples,durations=read_samples(run/"recent.csv")
    require(samples and durations,"逐块轨迹缺失")
    pending=[]; blocks={}; gates={}; proposal=None; decisions=[]; candidates=[]; files=[]; last=None; collections=0
    marker="V1真实决策：" if strategy=="v1" else "V1影子决策："
    for number,line in enumerate(lines,1):
        if "简单双因素候选：" in line: pending.append(fields(line,"简单双因素候选："))
        if "V1影子候选：" in line:
            b=fields(line,"V1影子候选：")
            if "当前RC" in b:
                rc,d10,d30=(int(b[k]) for k in ("当前RC","十秒真实增量","三十秒真实增量"))
                now,born,flags=(int(b[k]) for k in ("快照纳秒","代际起点纳秒","窗口标志"))
                require(born<=now and 0<=d10<=d30<=rc and flags & 1 and not flags & ~15,"候选窗口矛盾")
                require(not flags & 2 or now-born>=10*SECOND,"候选十秒窗口未预热")
                require(not flags & 4 or (now-born>=30*SECOND and flags & 2 and not flags & 8),"候选三十秒窗口矛盾")
                ch,lun,plane,block=map(int,b["PPA"].split("/"))
                require(0<=ch<8 and 0<=lun<4 and 0<=plane<2 and block==int(b["Zone"])//(256<<20) and
                        int(b["块编号"])==(ch*4+lun)*2+plane,"候选块编号或PPA错误")
                require(int(b["预测可知"])==bool(b["统计准入"]=="1" and eta(b) is not None),"预测可知标志不符")
                if eta(b) is not None: require(int(b["预测达阈值纳秒"])==eta(b),"候选原预测不符")
                blocks[int(b["Zone"])]=b
        if "V1影子文件：" in line: files.append(dict(fields(line,"V1影子文件："),行号=number))
        if "V1增长候选：" in line:
            g=fields(line,"V1增长候选："); zone=int(g["Zone"])
            require(zone not in gates,"增长候选重复"); gates[zone]=g
        if "V1增长决策：" in line: proposal=fields(line,"V1增长决策：")
        if marker in line:
            d=fields(line,marker)
            if strategy=="greedy":
                d=dict(d,GreedyZone=d["实际Greedy"],建议Zone=d["影子建议"],实际Zone=d["实际Greedy"],
                       建议额外有效搬运字节=d["额外有效搬运字节"],实际额外有效搬运字节="0")
            require(int(d["轮次"])==len(decisions)+1 and proposal and proposal["轮次"]==d["轮次"],"轮次或增长决策缺失")
            usable={z:b for z,b in blocks.items() if b["统计准入"]=="1"}
            eligible=[c for c in pending if qualifies(c)]
            greedy,suggest,would,_,predicted,_=predict(eligible,usable)
            require(tuple(int(proposal[k]) for k in ("原建议Zone","原会改选","原预测达阈值纳秒"))==
                    (suggest or 0,int(would),predicted),"原预测排序重算不符")
            admitted={}; greedy_valid=next((int(c["有效字节"]) for c in eligible if int(c["Zone"])==greedy),0)
            greedy_eta=eta(usable[greedy]) if greedy in usable else None
            require(set(gates)=={int(c["Zone"]) for c in pending},"候选与增长记录不一致")
            for c in pending:
                zone=int(c["Zone"]); b=blocks.get(zone); g=gates[zone]
                require(g["轮次"]==d["轮次"],"增长候选轮次不符")
                if b: require(int(b["原候选资格"])==qualifies(c),"原资格不符")
                passed=check_gate(b,g)
                if zone in usable and (zone==greedy or passed): admitted[zone]=b
                original_eta=eta(b) if b else None
                candidate=dict(轮次=d["轮次"],行号=number,Zone=zone,
                    有效字节=int(c["有效字节"]),原资格=qualifies(c),统计准入=zone in usable,
                    是Greedy=zone==greedy,原预测纳秒=original_eta,增长准入=passed,
                    额外有效字节=max(0,int(c["有效字节"])-greedy_valid),近期统计=b,增长证据=g)
                candidate["原预测拦截近期紧迫候选"]=bool(greedy_eta is not None and greedy_eta>10*SECOND and zone!=greedy and
                    qualifies(c) and zone in usable and passed and original_eta is not None and
                    original_eta>10*SECOND and candidate["额外有效字节"]<=EXTRA)
                candidates.append(candidate)
            switched=decision_check(pending,admitted,d)
            require(strategy!="greedy" or not switched,"基线执行了改选")
            require(proposal["准入后建议Zone"]==d["建议Zone"] and proposal["准入后会改选"]==d["会改选"],"执行建议不一致")
            d.update(行号=number,独立核验改选=switched,无Greedy预期集合数=empty_collection_count(pending)); decisions.append(d); last=d
            pending=[]; blocks={}; gates={}; proposal=None
        if "简单双因素实际集合：" in line:
            require(last is not None,"实际集合缺少决策")
            c=fields(line,"简单双因素实际集合：")
            if last["存在Greedy"]=="1":
                require(c["实际选中"]=="1" and c["Zone"]==last["实际Zone"] and c["实际使用等权"]=="0","实际选择不符")
            else: require(int(c["实际选中"])==last["无Greedy预期集合数"],"原等待或预算回退集合不符")
            collections+=1; last=None
    require(decisions and not pending and not gates and last is None and len(decisions)==collections,"决策未闭合")
    zc=[tuple(map(int,m.groups())) for line in lines if (m:=ZC_STATS.search(line))]
    rr=[tuple(map(int,m.groups())) for line in lines if (m:=RR_STATS.search(line))]
    require([r[0] for r in zc]==list(range(1,len(zc)+1)) and [r[0] for r in rr]==list(range(1,len(rr)+1)),"维护序号不连续")
    require(all(r[2]==1 for r in zc+rr) and text.count("RR 完成：")==len(rr),"源Reset或RR完成缺失")
    copies=[]; commits=[]; retired=[]
    for number,line in enumerate(lines,1):
        for marker,target in (("V1后续迁移复制：",copies),("V1后续映射提交：",commits),("V1后续文件退役：",retired)):
            if marker in line: target.append(dict(fields(line,marker),行号=number))
    require(all(c["复制成功"]=="1" for c in copies) and all(c["成功"]=="1" for c in commits),"复制或提交失败")
    require(Counter(c["文件编号"] for c in copies)==Counter(c["文件编号"] for c in commits),"复制和映射提交不匹配")
    copied=sum(int(c["有效字节"]) for c in copies); require(copied==sum(r[1] for r in zc+rr),"迁移总账不符")
    counts=Counter(); repeated=0
    for c in copies:
        if counts[c["文件编号"]]: repeated+=int(c["有效字节"])
        counts[c["文件编号"]]+=1
    blocked=[c for c in candidates if c["原预测拦截近期紧迫候选"]]
    for c in blocked:
        outcome=same_generation_outcome(lines,c["行号"],c["Zone"])
        if "行号" in outcome:
            before=PREFIX.match(lines[c["行号"]-1]); after=PREFIX.match(lines[outcome["行号"]-1])
            outcome["间隔秒"]=(datetime.strptime(after[1],"%Y/%m/%d-%H:%M:%S.%f")-datetime.strptime(before[1],"%Y/%m/%d-%H:%M:%S.%f")).total_seconds()
        c["后续同代维护观测"]=outcome
    times=defaultdict(list); responses=defaultdict(list)
    for r in operations:
        if int(r["代号"])>=0 and r["操作"] in ("读4KiB","写1MiB"):
            duration=int(r["延迟纳秒"]); times[r["操作"]].append(duration)
            responses[r["操作"]].append(duration+int(r["排程迟到纳秒"]))
    identities={name:digest for digest,name in (line.split() for line in (run/"identity.initial.sha256").read_text().splitlines())}
    software={name:digest for name,digest in identities.items() if not name.startswith("/home/femu/")}
    for suffix in ("stream_rrzc_test","stream_rrzc_test.cc","run_v1_growth_stream_guest.sh"):
        software[suffix]=next(digest for name,digest in identities.items() if name.endswith("/"+suffix))
    ledger=switch_ledger(decisions,files,copies,retired)
    return {"核验通过":True,"策略":strategy,"配置":config[0],"软件身份":software,"每线程请求轨迹SHA256":traces,
        "ZC次数":len(zc),"RR次数":len(rr),"ZC迁移MiB":sum(r[1] for r in zc)/(1<<20),
        "RR迁移MiB":sum(r[1] for r in rr)/(1<<20),"总维护迁移MiB":copied/(1<<20),
        "重复文件复制MiB":repeated/(1<<20),"预留替换次数":text.count("迁移预留替换："),
        "预留源额外Finish次数":text.count("RR 封闭专用预留源："),
        "实际改选次数":sum(d["独立核验改选"] for d in decisions),"决策":decisions,
        "原预测拦截近期紧迫候选":blocked,"改选后补清观测":ledger,
        "补清成本可验证":bool(ledger),"业务API耗时":{k:latency(v) for k,v in times.items()},
        "排程至API完成耗时":{k:latency(v) for k,v in responses.items()},
        "终态空间":rows(run/"states.csv")[-1],"逐块采样批次数":len(durations),
        "完整有效数据校验MiB":656,
        "说明":"单次180秒配对，非稳态。无改选不能验证补清成本；后续RR开始不等于准确达阈值时刻，未据地址关联宣称避免RR。"}


def compare(a,b):
    require(a["策略"]=="greedy" and b["策略"]=="v1" and a["核验通过"] and b["核验通过"],"不是有效Greedy/V1对照")
    for key in ("软件身份","每线程请求轨迹SHA256"):
        require(a[key]==b[key],"两组条件不同："+key)
    require({k:v for k,v in a["配置"].items() if k!="策略"}=={k:v for k,v in b["配置"].items() if k!="策略"},"参数不同")
    return {"核验通过":True,"Greedy":a,"V1":b,"RR减少次数":a["RR次数"]-b["RR次数"],
        "总维护迁移变化MiB（V1减Greedy）":b["总维护迁移MiB"]-a["总维护迁移MiB"],
        "两组终态空间一致":{k:v for k,v in a["终态空间"].items() if k!="经过纳秒"}==
            {k:v for k,v in b["终态空间"].items() if k!="经过纳秒"},"说明":"零干预只能定位入口限制，不证明机制收益或稳态。"}


if __name__=="__main__":
    require(len(sys.argv) in (2,3),"提供一个结果目录核验，或按Greedy/V1顺序提供两个目录比较")
    first=analyze_run(Path(sys.argv[1]))
    print(json.dumps(first if len(sys.argv)==2 else compare(first,analyze_run(Path(sys.argv[2]))),ensure_ascii=False,indent=2))
