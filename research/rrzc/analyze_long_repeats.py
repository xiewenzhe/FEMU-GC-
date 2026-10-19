#!/usr/bin/env python3
"""只读重验旧长测并核算重复复制；封闭先后关系不等于可避免成本。"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from statistics import mean

from analyze_stream import analyze, validate_pair, MARKER, MIB
from analyze_maintenance_overlap import PREFIX, CANDIDATE, RR_START, RR_STATS
from analyze_controlled_zc import ZC_STATS

ROOT = Path(__file__).resolve().parent
ANCHOR = re.compile(r"/rrzc-stream/g\d+\.(cold|hot)\.anchor\.sst")
RESET = re.compile(r"Zone 重置诊断：Zone=(\d+)，状态=成功，主机代际=(\d+)/(\d+)，")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def stamp(value):
    return datetime.strptime(value, "%Y/%m/%d-%H:%M:%S.%f").timestamp()


def file_size(name):
    """只支持归档载荷中不重建的单片段直接写锚点。"""
    match = ANCHOR.fullmatch(name)
    require(match is not None, "不是已知锚点，不能按文件名推算载荷")
    return (16 if match[1] == "cold" else 32) * MIB


def parse_ledger(lines, origin):
    """缺少FID的旧日志使用唯一创建名；源区必须有同代Reset证据。"""
    generations = defaultdict(int)
    creations, retired, tails, closures = {}, {}, {}, {}
    records, batches = [], []
    active, copying, selected = None, None, None
    candidates = {}

    def point(n, prefix):
        return {"行号": n, "经过秒": stamp(prefix[1]) - origin}

    def close_pool(key, kind, n, prefix, tail):
        require(key not in closures, "同区同代重复封闭")
        closures[key] = dict(point(n, prefix), 类型=kind, 尾部字节=tail)

    for n, line in enumerate(lines, 1):
        p = PREFIX.match(line)
        if not p:
            continue
        message, thread = p[3], p[2]
        m = re.search(r"New writable file: (\S+) direct: (\d+)", message)
        if m and ANCHOR.fullmatch(m[1]):
            require(m[1] not in creations and m[2] == "1", "锚点重建或不是直接写，身份不再可靠")
            creations[m[1]] = n
        m = re.search(r"DeleteFile: (\S+)\.retired\.bin\s*$", message)
        if m and ANCHOR.fullmatch(m[1]):
            require(m[1] not in retired, "锚点重复退役")
            retired[m[1]] = point(n, p)
        m = CANDIDATE.fullmatch(message)
        if m:
            candidates[int(m[1])] = {"行号": n, "有效字节": int(m[3]),
                                      "最大RC": int(m[5]) if m[5] else None}
        m = re.search(r"简单双因素实际集合：.*实际选中=(\d+)，Zone=(\d+)，", message)
        if m:
            require(active is None and copying is None, "维护尚未结束就出现新选区")
            selected = int(m[2]) if m[1] == "1" else None
        if "GC 选区汇总：" in message:
            m = re.search(r"选中Zone=(\d+)，", message)
            require(m is not None and int(m[1]) == int(selected is not None), "不支持批量或选区不一致")
            if selected is not None:
                active = dict(point(n, p), 类型="ZC", 源Zone=selected,
                              源代际=generations[selected], 线程=thread,
                              候选=candidates[selected], 文件=[])
            candidates = {}
            selected = None
        m = RR_START.fullmatch(message)
        if m:
            require(active is None, "RR与已有维护交叠")
            source = int(m[1])
            active = dict(point(n, p), 类型="RR", 源Zone=source,
                          源代际=generations[source], 线程=thread, 文件=[],
                          达阈值块=int(m[2]), 源封闭尾部字节=int(m[4]))
        m = re.search(r"迁移预留替换：旧Zone=(\d+)，新Zone=(\d+)", message)
        if m:
            old, new = map(int, m.groups())
            key = (old, generations[old])
            require(copying is not None and key in tails and 0 < tails[key] < copying["字节"],
                    "预留替换缺少已知不足尾部")
            close_pool(key, "提前替换Finish", n, p, tails[key])
            require((new, generations[new]) not in closures, "新预留未Reset便复用")
        m = re.search(r"RR 封闭专用预留源：Zone=(\d+)，封闭剩余=(\d+) 字节", message)
        if m:
            source, tail = map(int, m.groups())
            key = (source, generations[source])
            require(key in tails and tails[key] == tail, "RR封闭尾部与写入账不一致")
            close_pool(key, "预留源RR封闭", n, p, tail)
        m = re.fullmatch(r"MigrateFileExtents, fname: (\S+), extent count: (\d+)", message)
        if m:
            require(active is not None and active["线程"] == thread and copying is None,
                    "复制没有唯一同线程维护上下文")
            name = m[1]
            require(m[2] == "1" and name in creations and name not in retired, "单片段／文件生命周期未确认")
            copying = dict(point(n, p), 文件名=name, 字节=file_size(name),
                           源Zone=active["源Zone"], 源代际=active["源代际"], 类型=active["类型"])
        m = re.search(r"迁移使用专用预留：Zone=(\d+)，需要=(\d+)，剩余=(\d+)", message)
        if m:
            require(copying is not None and "目标Zone" not in copying, "目标不唯一")
            target, size, tail = map(int, m.groups())
            key = (target, generations[target])
            require(size == copying["字节"] and size <= tail <= 256*MIB and
                    key not in closures and (key not in tails or tails[key] == tail), "物理申请或目标尾部不一致")
            copying.update(目标Zone=target, 目标代际=key[1], 目标申请行号=n, 目标剩余字节=tail-size)
        m = re.fullmatch(r"TakeMigrateZone: (\d+)", message)
        if m:
            require(copying is not None and copying.get("目标Zone") == int(m[1]) and
                    "取得行号" not in copying, "存在普通／重复目标，停止而非补造")
            copying["取得行号"] = n
        m = re.fullmatch(r"ReleaseMigrateZone: (\d+)", message)
        if m:
            require(copying is not None and copying.get("目标Zone") == int(m[1]) and
                    "取得行号" in copying and "释放行号" not in copying, "目标取得／释放不配对")
            copying["释放行号"] = n
            key = (copying["目标Zone"], copying["目标代际"])
            tails[key] = copying["目标剩余字节"]
            if tails[key] == 0:
                close_pool(key, "自然写满", n, p, 0)
        m = re.fullmatch(r"MigrateFileExtents Finished, fname: (\S+), extent count: (\d+)", message)
        if m:
            require(copying is not None and copying["文件名"] == m[1] and m[2] == "1" and
                    "释放行号" in copying, "文件迁移结束记录不配对")
            copying.update(结束行号=n, 结束秒=stamp(p[1])-origin)
            active["文件"].append(copying)
            records.append(copying)
            copying = None
        m = RESET.search(message)
        if m:
            zone, before, after = map(int, m.groups())
            require(generations[zone] == before and after == before+1 and "代际可信=1" in message,
                    "Reset代际跳变或不可信")
            generations[zone] = after
        stats = ZC_STATS.search(message) or RR_STATS.search(message)
        if stats:
            require(active is not None and copying is None and active["线程"] == thread,
                    "维护统计缺少同线程完整复制")
            kind = "ZC" if ZC_STATS.search(message) else "RR"
            number, size, resets = map(int, stats.groups()[:3])
            require(kind == active["类型"] and size == sum(c["字节"] for c in active["文件"]) and
                    resets == 1 and generations[active["源Zone"]] == active["源代际"]+1,
                    "维护字节／源Reset未与逐文件账闭合")
            active.update(统计行号=n, 完成秒=stamp(p[1])-origin, 字节=size, 轮次=number)
            for record in active["文件"]:
                record.update(维护完成秒=active["完成秒"], 维护统计行号=n)
            batches.append(active)
            active = None
    require(active is None and copying is None and records, "日志截断或没有迁移")
    previous, counts = {}, Counter()
    for record in records:
        name = record["文件名"]
        key = (record["源Zone"], record["源代际"])
        record["文件第几次复制"] = counts[name]+1
        if name in previous:
            last = previous[name]
            require(key == (last["目标Zone"], last["目标代际"]), "重搬源并非上次同代目标")
            closure = closures.get(key)
            require(closure is not None and closure["行号"] < record["行号"], "重搬源封闭原因未知")
            record.update(此前复制行号=last["行号"], 源封闭=closure,
                          距上次复制秒=record["经过秒"]-last["结束秒"])
        record["退役"] = retired.get(name)
        if name in retired:
            require(retired[name]["行号"] > record["结束行号"], "迁移发生在文件退役后")
            record["距退役秒"] = retired[name]["经过秒"]-record["结束秒"]
        counts[name] += 1
        previous[name] = record
    return {"复制": records, "维护": batches, "封闭": [dict(Zone=k[0], 代际=k[1], **v) for k,v in closures.items()]}


def measured(records, warmup, seconds):
    """与原统计一致按维护完成时间入窗；首次／重复按全程历史定义。"""
    return [c for c in records if warmup <= c["维护完成秒"] < seconds]


def summarize(records):
    groups = defaultdict(lambda: {"次数": 0, "MiB": 0})
    for c in records:
        label = "首次" if c["文件第几次复制"] == 1 else c["源封闭"]["类型"]+"后"+c["类型"]
        groups[label]["次数"] += 1
        groups[label]["MiB"] += c["字节"]/MIB
    return dict(groups)


def audit_run(run, checked):
    lines = (run/"zenfs.run.log").read_text().splitlines()
    origins = [stamp(p[1])-int(m[1])/1e9 for line in lines
               if (p := PREFIX.match(line)) and (m := MARKER.search(line))]
    require(origins and max(origins)-min(origins) <= .020, "时钟对齐不稳定")
    ledger = parse_ledger(lines, mean(origins))
    # 原核验器重新检查全部哈希、固定请求、软件、评分、数据校验和维护总账。
    events = checked["维护事件"]
    require(len(events) == len(ledger["维护"]), "维护数量与原核验器不同")
    for a,b in zip(sorted(events,key=lambda e:e["行号"]), ledger["维护"]):
        require(a["类型"] == b["类型"] and a["迁移字节"] == b["字节"] and
                abs(a["经过秒"]-b["完成秒"]) < .002, "维护时点或字节与原核验器不符")
    config = checked["配置"]
    records = measured(ledger["复制"], int(config["预热秒数"]), int(config["计划业务秒数"]))
    require(sum(c["字节"] for c in records) == checked["预热后维护"]["总维护迁移字节"], "预热后账目未闭合")
    return {"来源": str(run), "日志SHA256": hashlib.sha256((run/"zenfs.run.log").read_bytes()).hexdigest(),
            "原始记录重新核验通过": True, "预热后分项": summarize(records), "预热后复制": records,
            "全程逐文件账": ledger, "终态空间": checked["终态空间"]}


def compare(a,b):
    files = {}
    def account(records):
        result = defaultdict(lambda: {"总MiB": 0, "首次MiB": 0, "重复MiB": 0, "分项": Counter()})
        for c in records:
            r = result[c["文件名"]]
            r["总MiB"] += c["字节"]/MIB
            key = "首次MiB" if c["文件第几次复制"] == 1 else "重复MiB"
            r[key] += c["字节"]/MIB
            label = "首次" if key == "首次MiB" else c["源封闭"]["类型"]+"后"+c["类型"]
            r["分项"][label] += c["字节"]/MIB
        return result
    aa,bb = account(a["预热后复制"]), account(b["预热后复制"])
    delta = Counter()
    for name in sorted(set(aa)|set(bb)):
        x,y = aa[name],bb[name]
        files[name] = {"Greedy": x, "加权": y, "净迁移变化MiB": y["总MiB"]-x["总MiB"]}
        for kind in set(x["分项"])|set(y["分项"]):
            delta[kind] += y["分项"][kind]-x["分项"][kind]
    return {"Greedy": a, "加权": b, "逐文件配对": files,
            "预热后分项差MiB（加权减Greedy）": dict(delta),
            "净迁移变化MiB": sum(f["净迁移变化MiB"] for f in files.values())}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = {"限制": ["旧日志无FID及逐片段提交；仅唯一创建、单片段直接写、全部维护成功且字节闭合的载荷可用本分析。",
                       "同名业务锚点跨配对匹配，单组链用目标Zone及主机Reset代际校验；不代表设备永久身份。",
                       "封闭后再次复制是已发生路径，不证明仅阻止该次Finish便可避免全部重搬。",
                       "RR实发说明设备已达阈值，不证明RR在另一选区反事实中仍必需或可以省掉。",
                       "预热后按原维护完成时刻入窗，复制序次使用全程历史；终态空间差异不补偿。",
                       "只做历史账目，不补造旧日志缺少的同块近期增量或回放新选择器。"], "配对": {}}
    for name in ("stream-5050-long-20261012", "stream-8020-long-20261013"):
        parent = ROOT/"results"/name
        runs = [parent/mode/(name+"-"+mode) for mode in ("valid","weighted")]
        checked = [analyze(run) for run in runs]
        validate_pair(checked)
        result["配对"][name] = compare(*(audit_run(run,r) for run,r in zip(runs,checked)))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
