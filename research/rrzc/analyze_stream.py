#!/usr/bin/env python3
"""只读核验滚动并发载荷；保留完整账目，不把维护次数多等同于稳态。"""
import argparse
import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

from analyze_periodic import latency, cv, validate_decisions
from analyze_controlled_choice import selection_view, zone_shape
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import PREFIX, parse_lines

MIB = 1 << 20
WINDOW = 300
# 采样记录的时间是多次设备查询开始，查询结束才写日志，不能用于时钟对齐。
# 更新提交标记在记录日志时直接取单调时钟，两者间只有函数调用开销。
MARKER = re.compile(r"滚动更新提交：代号=\d+，经过纳秒=(\d+)")


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def expected_reads(seconds):
    """独立重建固定读计划；不从真实RR或选区结果生成请求。"""
    result = []
    for g in range(seconds // 30):
        for start, count, rate, phase in ((10, 3584, 512, "阈值以下热读"),
                                         (17, 172, 4, "持续背景读"),
                                         (60, 768, 32, "更新后热读"),
                                         (84, 144, 4, "持续背景读")):
            for i in range(count):
                due = (30*g+start)*10**9+i*10**9//rate
                if due >= seconds*10**9:
                    break
                for kind in (0, 1):
                    result.append((due, g, kind, phase, 0 if kind else (i % 8)*65536))
    return sorted(result, key=lambda r: r[:3])


def check_trace(operations, seconds):
    traces = [hashlib.sha256(), hashlib.sha256()]
    serials = [0, 0]
    reads = []
    writes = defaultdict(list)
    for r in operations:
        thread, serial = int(r["线程"]), int(r["序号"])
        if thread not in (0, 1) or serial != serials[thread] or r["成功"] != "1":
            raise ValueError("线程序号不连续或存在失败请求")
        serials[thread] += 1
        fields = [r[k] for k in ("阶段", "操作", "代号", "文件", "偏移", "字节")]
        traces[thread].update((json.dumps(fields, ensure_ascii=False)+"\n").encode())
        g = int(r["代号"])
        if r["操作"] == "读4KiB":
            kind = int(".hot." in r["文件"])
            if thread != 1 or int(r["字节"]) != 4096:
                raise ValueError("读线程或请求大小错误")
            reads.append((g, kind, r["阶段"], int(r["偏移"])))
        if r["操作"] == "写1MiB" and g >= 0:
            if thread != 0 or int(r["字节"]) != MIB:
                raise ValueError("写线程或请求大小错误")
            writes[g].append((r["文件"], int(r["偏移"])))
    expected = expected_reads(seconds)
    if reads != [r[1:] for r in expected]:
        raise ValueError("真实读请求未完整执行固定计划")
    if set(writes) != set(range(seconds // 30)):
        raise ValueError("代际写入缺失")
    for g, actual in writes.items():
        planned = set()
        for kind, anch, ver in (("cold", 16, 240), ("hot", 32, 224)):
            planned.update((f"/rrzc-stream/g{g}.{kind}.anchor.sst", i*MIB) for i in range(anch))
            planned.update((f"/rrzc-stream/g{g}.{kind}.version0.bin", i*MIB) for i in range(ver))
        if len(actual) != 512 or set(actual) != planned:
            raise ValueError("代际写入偏移或总量错误")
    return [h.hexdigest() for h in traces]


def stability(windows, mode):
    checks = {"至少六个完整计量窗口": len(windows) >= 6}
    for key in ("平均空闲字节", "平均有效字节", "平均满区可回收字节"):
        values = [w[key] for w in windows if w[key] is not None]
        checks[key+"窗口波动不超过一个Zone"] = bool(values) and max(values)-min(values) <= 256*MIB
    for key in ("ZC成功次数", "RR完成次数", "总维护迁移字节"):
        checks[key+"窗口变异系数不超过0.25"] = bool(windows) and cv([w[key] for w in windows]) <= .25
    totals = {k: sum(w[k] for w in windows) for k in ("ZC成功次数", "RR完成次数")}
    scale = totals["ZC成功次数"] >= 100 and (mode != "valid" or totals["RR完成次数"] >= 100)
    return {"计量区间维护量": totals, "维护规模目标达到": scale, "窗口筛查": checks,
            "相对稳定筛查通过": all(checks.values()),
            "说明": "五分钟窗口的合成负载筛查，不等于真实RocksDB稳态；新机制RR为零不判为负载不足"}


def analyze(run):
    for name in ("mkfs.exit", "run.exit", "verify.exit", "runner.exit"):
        if (run/name).read_text().strip() != "0":
            raise ValueError(f"{name}未成功，不能纳入完整对照")
    identity = (run/"identity.initial.sha256").read_text()
    if identity != (run/"identity.final.sha256").read_text():
        raise ValueError("运行期间软件身份改变")
    root = Path("/home/femu/rrzc-stream-results")/run.name
    for line in (run/"logs.sha256").read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        relative = Path(filename).relative_to(root)
        if hashlib.sha256((run/relative).read_bytes()).hexdigest() != digest:
            raise ValueError(f"原始记录哈希不符：{relative}")
    config = rows(run/"config.csv")
    if len(config) != 1:
        raise ValueError("配置行数错误")
    config = config[0]
    seconds, warmup = int(config["计划业务秒数"]), int(config["预热秒数"])
    mode = config["策略"]
    if mode not in ("valid", "weighted") or config["RR阈值"] != "4096" or config["读写是否并发"] != "1":
        raise ValueError("实验策略或开关错误")
    text = (run/"zenfs.run.log").read_text()
    lines = text.splitlines()
    parsed = parse_lines(selection_view(lines), 64)
    weight = int(config.get("评分空间权重百分比", "50"))
    if int(config.get("评分读风险权重百分比", str(100-weight))) != 100-weight:
        raise ValueError("评分权重之和不等于100")
    if "评分空间权重百分比" in config:
        expected_weights = (weight, 100-weight) if mode == "weighted" else (100, 0)
        logged = set(re.findall(r"简单双因素：启用，.*有效量权重=(\d+)%，块读数权重=(\d+)%", text))
        if logged != {tuple(map(str, expected_weights))}:
            raise ValueError("配置权重与实际启用日志不相同")
    decisions = validate_decisions(lines, mode, int(config["回收预算字节"]), weight)
    if "[ERROR]" in text or parsed["failed_resets"] or any(e["status"] != "完成" for e in parsed["rr"]):
        raise ValueError("存在维护错误或未完成RR")
    if set(re.findall(r"GC 检查：.*启动水平=(\d+)%", text)) != {"20"}:
        raise ValueError("未确认GC20%")
    verification = (run/"zenfs.verify.log").read_text()
    verify_text = (run/"verify.stdout.log").read_text()
    expected_bytes = (min(seconds//30, 4)*48+464)*MIB
    if f"业务完整比对字节={expected_bytes}" not in verify_text or "[ERROR]" in verification or "RR 开始：" in verification or ZC_STATS.search(verification):
        raise ValueError("独立只读校验不符或引入额外维护")
    anchors = []
    for line in lines:
        prefix, marker = PREFIX.match(line), MARKER.search(line)
        if prefix and marker:
            anchors.append(datetime.strptime(prefix[1], "%Y/%m/%d-%H:%M:%S.%f").timestamp()-int(marker[1])/1e9)
    if not anchors or max(anchors)-min(anchors) > .020:
        raise ValueError("日志与单调时钟未稳定对齐")
    origin = mean(anchors)
    def elapsed(line):
        prefix = PREFIX.match(line)
        if not prefix:
            raise ValueError("维护事件没有时间戳")
        return datetime.strptime(prefix[1], "%Y/%m/%d-%H:%M:%S.%f").timestamp()-origin
    events = []
    for n, line in enumerate(lines, 1):
        match = ZC_STATS.search(line)
        if match:
            round_no, moved, resets = map(int, match.groups())
            events.append({"类型": "ZC", "经过秒": elapsed(line), "轮次": round_no,
                           "行号": n, "迁移字节": moved, "重置区数": resets})
    for e in parsed["rr"]:
        events.append({"类型": "RR", "经过秒": elapsed(lines[e["end_line"]-1]),
                       "Zone": e["zone"], "行号": e["end_line"], "迁移字节": e["moved"], "重置区数": e["resets"]})
    def totals(items):
        return {"ZC统计轮数": sum(e["类型"] == "ZC" for e in items),
                "ZC成功次数": sum(e["类型"] == "ZC" and e["重置区数"] > 0 for e in items),
                "RR完成次数": sum(e["类型"] == "RR" for e in items),
                "ZC迁移字节": sum(e["迁移字节"] for e in items if e["类型"] == "ZC"),
                "RR迁移字节": sum(e["迁移字节"] for e in items if e["类型"] == "RR"),
                "维护源重置区数": sum(e["重置区数"] for e in items),
                "总维护迁移字节": sum(e["迁移字节"] for e in items)}
    operations = rows(run/"operations.csv")
    trace = check_trace(operations, seconds)
    values, response, late = defaultdict(list), defaultdict(list), defaultdict(list)
    windows = [{"开始秒": start, "结束秒": min(start+WINDOW, seconds), "业务读字节": 0,
                "业务写字节": 0} for start in range(0, seconds, WINDOW)]
    window_latency = defaultdict(lambda: defaultdict(list))
    for r in operations:
        if int(r["代号"]) < 0 or r["操作"] not in ("读4KiB", "写1MiB"):
            continue
        at = int(r["开始纳秒"])/1e9
        idx = min(int(at//WINDOW), len(windows)-1)
        op, duration, delay = r["操作"], int(r["延迟纳秒"]), int(r["排程迟到纳秒"])
        windows[idx]["业务读字节" if op == "读4KiB" else "业务写字节"] += int(r["字节"])
        window_latency[idx][op].append(duration)
        if warmup <= at < seconds:
            values[op].append(duration)
            response[op].append(duration+delay)
            late[op].append(delay)
    states = [{k: int(v) for k, v in r.items()} for r in rows(run/"states.csv")]
    for idx, w in enumerate(windows):
        w.update(totals([e for e in events if w["开始秒"] <= e["经过秒"] < w["结束秒"]]))
        for key in ("空闲字节", "有效字节", "满区可回收字节"):
            samples = [r[key] for r in states if w["开始秒"] <= r["经过纳秒"]/1e9 < w["结束秒"]]
            w["平均"+key] = mean(samples) if samples else None
        w["API延迟"] = {k: latency(v) for k, v in window_latency[idx].items()}
    measured = [w for w in windows if w["开始秒"] >= warmup and w["结束秒"]-w["开始秒"] == WINDOW]
    resets = list(map(int, re.findall(r"Zone 重置诊断：Zone=(\d+)，状态=成功，", text)))
    all_totals = totals(events)
    io_resets = sum(z >= 3*256*MIB for z in resets)
    if io_resets < all_totals["维护源重置区数"]:
        raise ValueError("主机IO区重置总账小于维护源重置")
    all_totals.update({"所有主机IO区成功重置次数": io_resets,
                       "非ZC非RR成功重置次数": io_resets-all_totals["维护源重置区数"],
                       "元数据区成功重置次数": len(resets)-io_resets})
    final = states[-1]
    final["部分区未回收无效字节"] = 29*256*MIB-final["空闲字节"]-final["有效字节"]-final["满区可回收字节"]
    if final["部分区未回收无效字节"] < 0:
        raise ValueError("终态空间账目不符")
    return {"来源": str(run), "配置": config, "软件身份": identity, "每线程实际轨迹sha256": trace,
            "全程维护": all_totals, "预热后维护": totals([e for e in events if warmup <= e["经过秒"] < seconds]),
            "每五分钟窗口": windows, "相对稳定": stability(measured, mode), "终态空间": final,
            "终态IO区形态": zone_shape(json.loads((run/"zones.final.json").read_text())["zone_list"]),
            "预热后API延迟": {k: latency(v) for k, v in values.items()},
            "预热后排程到API完成响应延迟": {k: latency(v) for k, v in response.items()},
            "预热后排程迟到": {k: latency(v) for k, v in late.items()},
            "维护事件": events, "逐轮评分与选区复核": decisions,
            "限制": "同步ZenFS合成文件API，不是真实RocksDB或NAND延迟；排程响应包含线程排队，计时窗口外事件仍保留全账"}


def validate_pair(runs):
    if len(runs) != 2 or {r["配置"]["策略"] for r in runs} != {"valid", "weighted"}:
        raise ValueError("需要两种策略各一组")
    a, b = runs
    for key in ("软件身份", "每线程实际轨迹sha256"):
        if a[key] != b[key]:
            raise ValueError(f"两组{key}不相同")
    if {k: v for k, v in a["配置"].items() if k != "策略"} != {k: v for k, v in b["配置"].items() if k != "策略"}:
        raise ValueError("两组计划参数不一致")
    keys = ("空闲字节", "有效字节", "满区可回收字节", "部分区未回收无效字节")
    return {"软件和逻辑请求轨迹相同": True,
            "终态空间一致": all(a["终态空间"][k] == b["终态空间"][k] for k in keys),
            "说明": "终态未偿清空间差异单列；这是串行单次配对，尚无独立重复或统计显著性结论"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    args = parser.parse_args()
    result = {"运行": [analyze(p) for p in args.runs]}
    if len(result["运行"]) == 2:
        result["配对核验"] = validate_pair(result["运行"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
