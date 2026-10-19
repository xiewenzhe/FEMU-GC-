#!/usr/bin/env python3
"""核验预注册180秒或900秒新入口配对，联合报告维护、净搬运及空间。"""
import hashlib
import json
from pathlib import Path
import re
import sys
from collections import Counter,defaultdict
from statistics import mean
from analyze_stream import rows,check_trace
from analyze_periodic import latency
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from analyze_v1_trajectory import read_samples
from analyze_v1_growth_stream import switch_ledger
from analyze_tail_stream import ledger
from analyze_v1_shadow_stream import require,fields
from guarded_rounds import parse
from analyze_short_shadow_stream import future_view
from analyze_v1_victim_value import stamp
from analyze_maintenance_overlap import PREFIX


def proposed_outcomes(run,lines,samples,decisions,files):
    # 只作事后观测，复用首次维护／退役／Reset截断，不输入选区器。
    blocks={}
    for line in lines:
        if "V1影子候选：" in line:
            b=fields(line,"V1影子候选：");blocks[(b["轮次"],b["Zone"])]=b
    host_times={(int(r["Zone"]),int(r["设备快照纳秒"])):int(r["查询结束纳秒"]) for r in rows(run/"recent.csv")}
    origins=[stamp(line)-int(m[1])/1e9 for line in lines
        if (m:=re.search(r"滚动更新提交：代号=\d+，经过纳秒=(\d+)",line))]
    require(origins and max(origins)-min(origins)<.020,"日志与业务时钟未稳定对齐")
    outcomes=[]
    for d in decisions:
        if d["原会改选"]!="1":continue
        b=blocks[(d["轮次"],d["短期建议Zone"])]
        o=dict(轮次=d["轮次"],决策行号=d["行号"],Zone=int(d["短期建议Zone"]),当前块=b,
            有效字节=int(b["有效字节"]),短期预测纳秒=int(d["预测达阈值纳秒"]),
            实际改选=d["实际改选"],尾部分类=d["尾部分类"])
        result=future_view(lines,samples,host_times,mean(origins),o,files)
        result["说明"]="实际改选后的原源已被ZC干预，不能用清理后RC推断未清理反事实；Greedy观测也截至首次维护或退役。"
        outcomes.append(result)
    return outcomes


def anchor_ledger(files,copies):
    names=defaultdict(set)
    # 有些RR先于候选资格形成；也从真实复制记录取得文件身份，避免漏账。
    for f in files+copies:
        if re.fullmatch(r"/rrzc-stream/g\d+\.(?:cold|hot)\.anchor\.sst",f.get("文件名","")):
            names[f["文件名"]].add(f["文件编号"])
    result={}
    for name,ids in names.items():
        require(len(ids)==1,"相同锚点路径对应多个文件身份")
        moves=[c for c in copies if c["文件编号"] in ids]
        result[name]=dict(文件编号=next(iter(ids)),维护次数=len(moves),
            迁移MiB=sum(int(c["有效字节"]) for c in moves)/(1<<20),
            维护类型=dict(Counter(c["维护类型"] for c in moves)),实际复制=moves)
    return result


def profile_for(run,strategy):
    # 只允许明确预注册目录，不按结果猜测负载或校验尺寸。
    if run.name in (f"guarded-stream-{strategy}-20261014",f"guarded-medium-20261015-{strategy}"):
        require(not (run/"victim-profile.csv").exists(),"原滚动组混入受控场景")
        return "original",656
    require(run.name==f"guarded-cases-{strategy}-20261014","不是独立预注册目录")
    require(rows(run/"victim-profile.csv")==[{"场景配置":"maintenance-cases","热读停止年龄秒":"65",
        "提前退役年龄秒":"66"}],"原停读／退役计划改变")
    return "maintenance-cases",624


def timing_for(run,strategy):
    if run.name==f"guarded-medium-20261015-{strategy}":return 900,180
    require(run.name in (f"guarded-stream-{strategy}-20261014",f"guarded-cases-{strategy}-20261014"),"未注册的计时计划")
    return 180,0


def window_account(lines,copies,operations,start,end):
    """复制序次按全程身份；入窗按维护完成时间，API按实际调用开始时间。"""
    origins=[stamp(line)-int(m[1])/1e9 for line in lines
             if (m:=re.search(r"滚动更新提交：代号=\d+，经过纳秒=(\d+)",line))]
    require(origins and max(origins)-min(origins)<.020,"计量时钟不稳定")
    origin=mean(origins); by_line={c["行号"]:c for c in copies}
    counts=Counter();pending=defaultdict(list);events=[];resets=0
    for n,line in enumerate(lines,1):
        p=PREFIX.match(line)
        if not p:continue
        if n in by_line:
            c=by_line[n];fid=c["文件编号"]
            pending[p[2]].append((int(c["有效字节"]),counts[fid]>0));counts[fid]+=1
        zc,rr=ZC_STATS.search(line),RR_STATS.search(line)
        if zc or rr:
            m=zc or rr; size,reset=map(int,m.groups()[1:3]);parts=pending.pop(p[2],[])
            require(sum(v for v,_ in parts)==size,"计量复制与维护完成字节不符")
            at=stamp(line)-origin
            if start<=at<end:
                events.append(dict(类型="ZC" if zc else "RR",字节=size,源Reset=reset,
                                   重复字节=sum(v for v,repeated in parts if repeated)))
        if re.search(r"Zone 重置诊断：Zone=(\d+)，状态=成功，",line):
            zone=int(re.search(r"Zone 重置诊断：Zone=(\d+)，",line)[1])
            if zone>=3*(256<<20) and start<=stamp(line)-origin<end:resets+=1
    require(not pending,"计量窗口有未归属复制")
    times=defaultdict(list);responses=defaultdict(list)
    for r in operations:
        if int(r["代号"])>=0 and r["操作"] in ("读4KiB","写1MiB") and start<=int(r["开始纳秒"])/1e9<end:
            duration=int(r["延迟纳秒"]);times[r["操作"]].append(duration)
            responses[r["操作"]].append(duration+int(r["排程迟到纳秒"]))
    total=sum(e["字节"] for e in events);repeated=sum(e["重复字节"] for e in events)
    return dict(开始秒=start,结束秒=end,ZC次数=sum(e["类型"]=="ZC" for e in events),
        RR次数=sum(e["类型"]=="RR" for e in events),总迁移MiB=total/(1<<20),
        首次迁移MiB=(total-repeated)/(1<<20),重复迁移MiB=repeated/(1<<20),
        维护源Reset=sum(e["源Reset"] for e in events),全部IO区Reset=resets,
        业务API耗时={k:latency(v) for k,v in times.items()},
        排程至API完成耗时={k:latency(v) for k,v in responses.items()})


def analyze(run,allow_final_overrun=False,tail_ablation=None,reopen_fixed=False,fixed_greedy=False):
    config=rows(run/"config.csv");require(len(config)==1,"配置不唯一")
    strategy=config[0]["策略"];require(strategy in ("greedy","guarded"),"策略未知")
    profile,verify_mib=profile_for(run,strategy)
    seconds,warmup=timing_for(run,strategy)
    require(not reopen_fixed or tail_ablation is not None,"修复版仅用于明确登记的尾部消融")
    if fixed_greedy:
        require(strategy=="greedy" and seconds==900 and
            run.parent.name=="tail-ablation-fixed-20261015-greedy" and
            tail_ablation is None and not reopen_fixed and not allow_final_overrun,
            "修复版Greedy目录或入口不符")
    if tail_ablation is not None:
        family="tail-ablation-fixed-20261015" if reopen_fixed else "tail-ablation-medium-20261015"
        require(tail_ablation in ("keep","bypass") and strategy=="guarded" and seconds==900 and
            run.parent.name==f"{family}-{tail_ablation}" and not allow_final_overrun,
            "消融目录或入口不符，不能混用旧事后边界例外")
    require(not allow_final_overrun or seconds==900,"只允许已注册900秒运行的显式边界复核")
    require(config==[{"策略":strategy,"计划业务秒数":str(seconds),"RR阈值":"4096","GC启动比例":"20",
        "回收预算字节":"201326592","读写是否并发":"1","预热秒数":str(warmup),"紧迫界限秒数":"10",
        "额外有效搬运预算字节":"16777216","观察采样毫秒":"500"}],"预先固定计划改变")
    for name in ("mkfs.exit","run.exit","verify.exit","runner.exit"):
        require((run/name).read_text().strip()=="0",name+"未成功")
    require((run/"identity.initial.sha256").read_bytes()==(run/"identity.final.sha256").read_bytes(),"运行期间源或库改变")
    guest=Path("/home/femu/rrzc-stream-results")/run.name
    for line in (run/"logs.sha256").read_text().splitlines():
        digest,filename=line.split(maxsplit=1)
        require(hashlib.sha256((run/Path(filename).relative_to(guest)).read_bytes()).hexdigest()==digest,"原始文件哈希不符")
    identities={name:digest for digest,name in (s.split() for s in (run/"identity.initial.sha256").read_text().splitlines())}
    software={Path(name).name if name.startswith("/home/femu/") else name:digest for name,digest in identities.items()}
    require(software["stream_rrzc_test.cc"]=="277ce66cf22a0a665eec2cf23d504e83058165971df6d5c5c3911b74868d276e","修改了原业务程序")
    text=(run/"zenfs.run.log").read_text();lines=text.splitlines()
    require("[ERROR]" not in text and "状态=失败" not in text,"维护存在错误")
    require(text.count("独立尾部约束：已启用")==1 and text.count("V1增长准入：已启用")==1,"入口或采样未唯一启用")
    require(f"独立尾部约束：已启用，真实执行={int(strategy=='guarded')}，" in text,"模式与日志不符")
    require(not any(s in text for s in ("V1真实：已启用","V1影子：已启用","短期影子：已启用","成本观察：已启用","ZC实验单次选区：已启用")),"混用旧入口")
    require(set(re.findall(r"GC 检查：.*启动水平=(\d+)%",text))=={"20"},"原GC20%未确认")
    operations=rows(run/"operations.csv");traces=check_trace(operations,seconds,profile)
    for r in operations:require(int(r["延迟纳秒"])>=0 and int(r["排程迟到纳秒"])>=0,"API计时非法")
    verify=(run/"verify.stdout.log").read_text();verify_log=(run/"zenfs.verify.log").read_text()
    require(f"业务完整比对字节={verify_mib*(1<<20)}" in verify and "填充文件首4KiB抽检=20" in verify,"有效数据完整校验未通过")
    require(not any(s in verify_log for s in ("GC 检查：","RR 开始：","独立约束决策：","[ERROR]")),"校验阶段引入维护或错误")
    samples,batches=read_samples(run/"recent.csv")
    require(samples,"逐块轨迹为空")
    if tail_ablation is not None or fixed_greedy:
        from ablation_sampling import audit_sampling
        sampling_audit=audit_sampling(run,samples,batches)
    elif allow_final_overrun:
        from guarded_sampling import audit_sampling
        sampling_audit=audit_sampling(run,samples,batches,seconds,True)
    else:
        require(len(batches)==seconds*2,"500ms完整32区轨迹缺失")
        sampling_audit=dict(实际批次=len(batches),原严格批数通过=True,事后边界复核启用=False)
    decisions,files,maintenance=parse(lines,strategy,tail_ablation)
    zc=[tuple(map(int,m.groups())) for line in lines if (m:=ZC_STATS.search(line))]
    rr=[tuple(map(int,m.groups())) for line in lines if (m:=RR_STATS.search(line))]
    require([r[0] for r in zc]==list(range(1,len(zc)+1)) and [r[0] for r in rr]==list(range(1,len(rr)+1)),"维护序号不连续")
    require(all(r[2]==1 for r in zc+rr) and text.count("RR 完成：")==len(rr),"维护源Reset或RR完成缺失")
    require(len(maintenance)==len(zc) and all(int(m["本轮迁移字节"])==r[1] for m,r in zip(maintenance,zc)),"决策维护和ZC总账不符")
    events=ledger(lines);copied=sum(int(c["有效字节"]) for c in events["实际复制"])
    require(copied==sum(r[1] for r in zc+rr),"复制与RR/ZC总账不符")
    anchors=anchor_ledger(files,events["实际复制"])
    require(sum(r["迁移MiB"] for r in anchors.values())==copied/(1<<20),"锚点账目未覆盖全部真实复制")
    retired=[dict(fields(line,"V1后续文件退役："),行号=n)
        for n,line in enumerate(lines,1) if "V1后续文件退役：" in line]
    stops=[dict(行号=n,原始日志=line) for n,line in enumerate(lines,1) if "受控热读停止：" in line]
    early=[dict(行号=n,原始日志=line) for n,line in enumerate(lines,1) if "受控文件退役完成：" in line]
    if profile=="maintenance-cases":
        require(any("代号=1，" in s["原始日志"] for s in stops) and
            any("代号=2，" in s["原始日志"] for s in stops) and len(early)==1 and
            "代号=2，" in early[0]["原始日志"],"原受控停止／退役事件未执行")
    moves=switch_ledger(decisions,files,events["实际复制"],retired)
    times=defaultdict(list);responses=defaultdict(list)
    for r in operations:
        if int(r["代号"])>=0 and r["操作"] in ("读4KiB","写1MiB"):
            duration=int(r["延迟纳秒"]);times[r["操作"]].append(duration)
            responses[r["操作"]].append(duration+int(r["排程迟到纳秒"]))
    suggested=[d for d in decisions if d["原会改选"]=="1"]
    return dict(核验通过=True,来源=str(run),策略=strategy,负载配置=profile,配置=config[0],软件身份=software,
        每线程请求轨迹SHA256=traces,ZC次数=len(zc),RR次数=len(rr),ZC迁移MiB=sum(r[1] for r in zc)/(1<<20),
        RR迁移MiB=sum(r[1] for r in rr)/(1<<20),总维护迁移MiB=copied/(1<<20),
        重复文件复制MiB=events["重复文件复制字节"]/(1<<20),预留替换次数=len(events["预留替换"]),
        预留源额外Finish次数=len(events["预留源RR封闭"]),实际改选次数=sum(d["独立核验改选"] for d in decisions),
        原短期建议次数=len(suggested),建议尾部分类=dict(Counter(d["尾部分类"] for d in suggested)),
        决策=decisions,改选后补清观测=moves,尾部事件账目=events,
        建议候选后续观测=(proposed_outcomes(run,lines,samples,decisions,files)
            if (tail_ablation is None and not fixed_greedy) or sampling_audit["逐块轨迹覆盖达标"] else
            [dict(状态="未知",原因="逐块轨迹存在超过2秒空档，不用于预测精度判断")]),
        锚点维护账目=anchors,
        业务API耗时={k:latency(v) for k,v in times.items()},排程至API完成耗时={k:latency(v) for k,v in responses.items()},
        实际受控停止事件=stops,实际提前退役事件=early,
        终态空间=rows(run/"states.csv")[-1],逐块采样批次数=len(batches),完整有效数据校验MiB=verify_mib,
        固定业务秒数=seconds,采样完整性核验=sampling_audit,
        预热后计量=window_account(lines,events["实际复制"],operations,warmup,seconds),
        说明=f"单次{seconds}秒同采样配对，非稳态或显著性性能实验；RR下降须联合净迁移与终态空间判断。")


def compare(a,b):
    require(a["核验通过"] and b["核验通过"] and a["策略"]=="greedy" and b["策略"]=="guarded","不是有效配对")
    for key in ("负载配置","软件身份","每线程请求轨迹SHA256"):require(a[key]==b[key],"两组条件不同："+key)
    require({k:v for k,v in a["配置"].items() if k!="策略"}=={k:v for k,v in b["配置"].items() if k!="策略"},"两组参数不同")
    anchors={name:dict(Greedy=a["锚点维护账目"].get(name),独立约束=b["锚点维护账目"].get(name),
        迁移变化MiB=(b["锚点维护账目"][name]["迁移MiB"]-a["锚点维护账目"][name]["迁移MiB"]
            if name in a["锚点维护账目"] and name in b["锚点维护账目"] else None))
        for name in sorted(set(a["锚点维护账目"])|set(b["锚点维护账目"]))}
    result=dict(核验通过=True,Greedy=a,独立约束=b,RR减少次数=a["RR次数"]-b["RR次数"],
        ZC增加次数=b["ZC次数"]-a["ZC次数"],总迁移变化MiB=b["总维护迁移MiB"]-a["总维护迁移MiB"],
        两组终态空间一致={k:v for k,v in a["终态空间"].items() if k!="经过纳秒"}=={k:v for k,v in b["终态空间"].items() if k!="经过纳秒"},
        同名锚点维护对照=anchors,
        说明=f"原请求轨迹相同，但单次{a.get('固定业务秒数',180)}秒配对不当作正式稳态收益。")
    if "预热后计量" in a and "预热后计量" in b:
        x,y=a["预热后计量"],b["预热后计量"]
        require((x["开始秒"],x["结束秒"])==(y["开始秒"],y["结束秒"]),"配对计量窗口不同")
        result["预热后净差（独立约束减Greedy）"]={k:y[k]-x[k] for k in
            ("ZC次数","RR次数","总迁移MiB","首次迁移MiB","重复迁移MiB","维护源Reset","全部IO区Reset")}
    return result


if __name__=="__main__":
    require(len(sys.argv) in (2,3),"输入单组结果，或按Greedy/独立约束顺序输入两组目录")
    first=analyze(Path(sys.argv[1]))
    print(json.dumps(first if len(sys.argv)==2 else compare(first,analyze(Path(sys.argv[2]))),ensure_ascii=False,indent=2))
