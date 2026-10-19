#!/usr/bin/env python3
"""只读重算真实影子建议与Greedy选择；观察后续事件，不推断避免RR的因果收益。"""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from analyze_periodic import latency, validate_decisions
from analyze_stream import check_trace
from analyze_controlled_choice import selection_view
from analyze_maintenance_overlap import PREFIX, parse_lines
from analyze_controlled_zc import ZC_STATS

SECOND = 10**9
MAX = 2**64-1
BUDGET = 192*(1<<20)
EXTRA = 16*(1<<20)


def require(ok, why):
    if not ok:
        raise ValueError(why)


def fields(line, marker):
    result = {}
    key = None
    for part in line.split(marker,1)[1].split("，"):
        if "=" in part:
            key, value = part.split("=",1)
            result[key] = value
        elif key:
            result[key] += "，"+part
    return result


def eta(block):
    rc, flags = int(block["当前RC"]), int(block["窗口标志"])
    if not flags & 1:
        return None
    if rc >= 4096:
        return 0
    if flags & 6 != 6:
        return None
    speed30 = min(int(block["十秒真实增量"])*3,int(block["三十秒真实增量"]))
    return min(MAX,((4096-rc)*30*SECOND+speed30-1)//speed30) if speed30 else MAX


def predict(eligible, usable):
    if not eligible:
        return None, None, False, 0, MAX, "没有合格候选"
    greedy = min(eligible,key=lambda c:(int(c["有效字节"]),int(c["Zone"])))
    zone = int(greedy["Zone"])
    if zone not in usable:
        return zone, zone, False, 0, MAX, "Greedy近期统计未知"
    g_eta = eta(usable[zone])
    if g_eta is None:
        return zone, zone, False, 0, MAX, "Greedy窗口不完整"
    if g_eta <= 10*SECOND:
        return zone, zone, False, 0, g_eta, "Greedy自身已经紧迫"
    best, reason = None, "没有近期紧迫候选"
    for c in eligible:
        other = int(c["Zone"])
        if other == zone or other not in usable:
            continue
        predicted = eta(usable[other])
        if predicted is None or predicted > 10*SECOND:
            continue
        extra = max(0,int(c["有效字节"])-int(greedy["有效字节"]))
        if extra > EXTRA:
            if best is None:
                reason = "紧迫候选超出额外有效搬运预算"
            continue
        key = (predicted,int(c["有效字节"]),other,extra)
        if best is None or key[:3] < best[:3]:
            best = key
    if best is None:
        return zone, zone, False, 0, MAX, reason
    return zone, best[2], True, best[3], best[0], "近期紧迫且额外有效搬运在预算内"


def analyze(run):
    profile="original"
    if (run/"victim-profile.csv").exists():
        with (run/"victim-profile.csv").open() as stream:
            require(list(csv.DictReader(stream))==[{"场景配置":"maintenance-cases","热读停止年龄秒":"65","提前退役年龄秒":"66"}],"受控场景配置不符")
        profile="maintenance-cases"
    for filename in ("mkfs.exit","run.exit","verify.exit","runner.exit"):
        require((run/filename).read_text().strip()=="0",filename+"未成功")
    require((run/"identity.initial.sha256").read_text()==(run/"identity.final.sha256").read_text(),
            "运行中软件身份改变")
    guest_root = Path("/home/femu/rrzc-stream-results")/run.name
    for line in (run/"logs.sha256").read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        local = run/Path(filename).relative_to(guest_root)
        require(hashlib.sha256(local.read_bytes()).hexdigest()==digest,"原始文件哈希不符："+str(local))
    with (run/"config.csv").open() as stream:
        config = list(csv.DictReader(stream))
    require(config==[{"实际策略":"Greedy","计划业务秒数":"180","RR阈值":"4096","GC启动比例":"20",
                     "回收预算字节":"201326592","影子界限秒数":"10","额外有效搬运预算字节":"16777216","实际改选":"0"}],
            "不是本轮预定影子配置")
    text = (run/"zenfs.run.log").read_text()
    lines = text.splitlines()
    require("V1影子：已启用" in text and "[ERROR]" not in text,"影子未启用或出现维护错误")
    require(set(re.findall(r"GC 检查：.*启动水平=(\d+)%",text))=={"20"},"GC20%未确认")
    actual = validate_decisions(lines,"valid",BUDGET)
    maintenance = parse_lines(selection_view(lines),64)
    require(not maintenance["failed_resets"] and all(x["status"]=="完成" for x in maintenance["rr"]),
            "RR或Reset存在失败")
    verify_text = (run/"verify.stdout.log").read_text()
    verify_log = (run/"zenfs.verify.log").read_text()
    expected_bytes=654311424 if profile=="maintenance-cases" else 687865856
    require(f"业务完整比对字节={expected_bytes}" in verify_text and "填充文件首4KiB抽检=20" in verify_text,
            "独立只读数据校验不符")
    require("RR 开始：" not in verify_log and not ZC_STATS.search(verify_log) and
            "V1影子决策：" not in verify_log and "[ERROR]" not in verify_log,"校验进程引入维护或影子判断")
    shadow, pending, blocks, candidate_rows, file_rows = [],[],{},[],[]
    last_decision = None
    for number,line in enumerate(lines,1):
        if "简单双因素候选：" in line:
            pending.append(fields(line,"简单双因素候选："))
        if "V1影子候选：" in line:
            b = fields(line,"V1影子候选：")
            candidate_rows.append(dict(b,行号=number))
            if "当前RC" in b:
                rc,d10,d30 = (int(b[k]) for k in ("当前RC","十秒真实增量","三十秒真实增量"))
                now,born,flags = (int(b[k]) for k in ("快照纳秒","代际起点纳秒","窗口标志"))
                require(born<=now and 0<=d10<=d30<=rc,"块窗口或计数矛盾")
                require(not flags & 2 or now-born>=10*SECOND,"十秒窗口预热标志矛盾")
                require(not flags & 4 or (flags & 2 and now-born>=30*SECOND and not flags & 8),"三十秒窗口标志矛盾")
                ch,lun,plane,block = map(int,b["PPA"].split("/"))
                require(0<=ch<8 and 0<=lun<4 and 0<=plane<2 and
                        int(b["块编号"])==(ch*4+lun)*2+plane and block==int(b["Zone"])//(256<<20),"块编号/PPA不一致")
                known = int(b["统计准入"]) and eta(b) is not None
                require(int(b["预测可知"])==bool(known),"候选预测可知标志不符")
                if eta(b) is not None:
                    require(int(b["预测达阈值纳秒"])==eta(b),"候选预计时间重算不符")
                if int(b["统计准入"]):
                    blocks[int(b["Zone"])] = b
        if "V1影子文件：" in line:
            file_rows.append(dict(fields(line,"V1影子文件："),行号=number))
        if "V1影子决策：" in line:
            d = fields(line,"V1影子决策：")
            eligible = [c for c in pending if int(c["容量字节"])-int(c["有效字节"])>=BUDGET and
                        int(c["覆盖资格"]) and int(c["空目标资格"])]
            expected = predict(eligible,blocks)
            observed = (int(d["实际Greedy"]) if int(d["存在Greedy"]) else None,
                        int(d["影子建议"]) if int(d["存在Greedy"]) else None,
                        bool(int(d["会改选"])),int(d["额外有效搬运字节"]),int(d["预测达阈值纳秒"]),d["原因"])
            require(expected==observed,"影子建议/预计时间/预算/原因重算不符")
            require(int(d["轮次"])==len(shadow)+1 and d["实际改选"]=="0" and
                    d["界限纳秒"]==str(10*SECOND) and d["预算字节"]==str(EXTRA),"轮次或实际改选/参数不符")
            shadow.append(dict(d,行号=number,可排序候选数=len(eligible)))
            last_decision = d
        if "简单双因素实际集合：" in line:
            collection = fields(line,"简单双因素实际集合：")
            require(last_decision is not None,"实际集合缺少此前影子记录")
            if last_decision["存在Greedy"]=="1":
                require(collection.get("Zone")==last_decision["实际Greedy"] and collection["实际选中"]=="1",
                        "影子运行改变了实际Greedy选择")
            pending,blocks,last_decision = [],{},None
    require(shadow and not pending and last_decision is None,"没有完整影子轮次或候选未闭合")
    zc = [tuple(map(int,m.groups())) for line in lines if (m:=ZC_STATS.search(line))]
    with (run/"operations.csv").open() as stream:
        operations = list(csv.DictReader(stream))
    traces = check_trace(operations,180,profile)
    times = defaultdict(list)
    for row in operations:
        if int(row["代号"])>=0 and row["操作"] in ("读4KiB","写1MiB"):
            times[row["操作"]].append(int(row["延迟纳秒"]))
    outcomes = []
    # 不只跟踪已建议改选的区；被预测判为不紧迫的其他合格区也要核对后续维护。
    watched = [(d,c) for d in shadow for c in candidate_rows if d["存在Greedy"]=="1" and
               c["轮次"]==d["轮次"] and c["Zone"]!=d["实际Greedy"] and
               c.get("原候选资格")=="1" and c.get("统计准入")=="1" and c.get("预测可知")=="1"]
    for decision,candidate in watched:
        start = decision["行号"]
        zone = candidate["Zone"]
        # 后续成功Reset结束当前物理代际；不能将重用此区后的RR归给旧候选。
        end = len(lines)
        for number,line in enumerate(lines[start:],start+1):
            if (match:=re.search(r"Zone 重置诊断：Zone=(\d+)，状态=成功，",line)) and match[1]==zone:
                end = number
                break
        events = []
        candidate_ids = {f["文件编号"] for f in file_rows if
                         f["轮次"]==decision["轮次"] and f["Zone"]==zone}
        retired = set()
        for number,line in enumerate(lines[start:end],start+1):
            if (match:=re.search(r"RR 开始：Zone (\d+)，",line)) and match[1]==zone:
                events.append((number,"RR开始",line))
            if "V1后续迁移复制：" in line:
                moved = fields(line,"V1后续迁移复制：")
                if moved["源Zone"]==zone and moved["复制成功"]=="1":
                    events.append((number,"维护数据复制（不等于提交成功）",line))
            if "V1后续文件退役：" in line:
                deleted = fields(line,"V1后续文件退役：")
                if deleted["文件编号"] in candidate_ids:
                    retired.add(deleted["文件编号"])
                    if retired==candidate_ids:
                        events.append((number,"候选文件最后链接全删除（不证明句柄均释放）",line))
        greedy = next(c for c in candidate_rows if c["轮次"]==decision["轮次"] and c["Zone"]==decision["实际Greedy"])
        item = {"影子轮次":decision["轮次"],"其他合格区":zone,
                "该区是否被建议改选":decision["会改选"]=="1" and decision["影子建议"]==zone,
                "当前RC":int(candidate["当前RC"]),"十秒增量":int(candidate["十秒真实增量"]),
                "三十秒增量":int(candidate["三十秒真实增量"]),
                "预测纳秒":int(candidate["预测达阈值纳秒"]),
                "额外有效搬运字节":max(0,int(candidate["有效字节"])-int(greedy["有效字节"]))}
        item["观测截至后续首次Reset或业务结束"] = end
        if events:
            number,kind,line = min(events)
            before = PREFIX.match(lines[start-1]); after = PREFIX.match(line)
            require(before and after,"事件缺少日志时间")
            delta = datetime.strptime(after[1],"%Y/%m/%d-%H:%M:%S.%f")-datetime.strptime(before[1],"%Y/%m/%d-%H:%M:%S.%f")
            item.update(首个后续维护观测=kind,行号=number,观测间隔秒=delta.total_seconds())
            if kind=="RR开始":
                item["预测晚于随后RR开始"] = item["预测纳秒"]/SECOND>delta.total_seconds()
        else:
            item["首个后续维护观测"]="业务结束前未见；不能据此认定避免RR"
        outcomes.append(item)
    return {"核验通过":True,"来源":str(run),"业务秒数":180,"受控负载配置":profile,"实际策略":"Greedy","实际改选次数":0,
            "影子轮次":len(shadow),"建议改选轮次":sum(d["会改选"]=="1" for d in shadow),
            "原因分布":dict(Counter(d["原因"] for d in shadow)),"影子决策":shadow,
            "候选观测":candidate_rows,"候选文件观测数":len(file_rows),"其他合格区后续观测":outcomes,
            "建议改选后续观测":[x for x in outcomes if x["该区是否被建议改选"]],
            "实际Greedy核验":actual,"ZC成功次数":sum(resets>0 for _,_,resets in zc),
            "RR完成次数":len(maintenance["rr"]),"总维护迁移字节":sum(moved for _,moved,_ in zc)+sum(e["moved"] for e in maintenance["rr"]),
            "全程业务API延迟":{op:latency(values) for op,values in times.items()},"请求计划哈希":traces,
            "真实改选收益已验证":False,"相对稳态或开销对照已验证":False}


if __name__ == "__main__":
    require(len(sys.argv)==2,"需要本轮原始结果目录")
    print(json.dumps(analyze(Path(sys.argv[1])),ensure_ascii=False,indent=2))
