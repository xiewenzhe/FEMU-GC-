#!/usr/bin/env python3
"""只读核验周期负载、真实维护日志与延迟；周期稳定不等于数据库稳态。"""
import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev

from analyze_controlled_choice import selection_view, zone_shape
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import PREFIX, parse_lines

MIB = 1 << 20
PERIOD = 80
WARMUP = 2
MARKER = re.compile(r"周期负载标记：周期=(\d+)，阶段=([^，]+)，经过纳秒=(\d+)")


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def percentile(values, p):
    """最近秩分位数；不插值，不把业务等待计入API延迟。"""
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(p*len(values))-1)]


def latency(values):
    return {"样本数": len(values), "平均微秒": mean(values)/1000 if values else None,
            "p50微秒": percentile(values, .50)/1000 if values else None,
            "p95微秒": percentile(values, .95)/1000 if values else None,
            "p99微秒": percentile(values, .99)/1000 if values else None,
            "最大微秒": max(values)/1000 if values else None}


def cv(values):
    average = mean(values)
    return pstdev(values)/average if average else 0.0


def validate_decisions(lines, mode, budget, space_weight=50):
    """逐轮重算归一化分数及排序；零候选空集合不算实际批量回退。"""
    if not isinstance(space_weight, int) or not 0 <= space_weight <= 100:
        raise ValueError("空间权重必须是0至100的整数")
    score_key = "等权分数ppm" if space_weight == 50 else "加权分数ppm"
    used_key = "实际使用等权" if space_weight == 50 else "实际使用加权"
    keys = ("Zone", "有效字节", "容量字节", "最大块读数", "读数阈值", "覆盖资格", "空目标资格",
            "读压力可用", "空间分量ppm", "风险分量ppm", score_key)
    pending, decisions, fallbacks, empty_rounds = [], [], 0, 0
    for line_no, line in enumerate(lines, 1):
        if "简单双因素候选：" in line:
            parts = line.split("简单双因素候选：", 1)[1].split("，")
            if len(parts) != len(keys): raise ValueError("简单双因素候选字段不全")
            c = {}
            for key, part in zip(keys, parts):
                if not part.startswith(key+"="): raise ValueError("候选字段顺序不符")
                c[key] = int(part.split("=", 1)[1])
            v, cap, t, r = (c[k] for k in ("有效字节", "容量字节", "读数阈值", "最大块读数"))
            if not 0 < v <= cap or any(c[k] not in (0, 1) for k in ("覆盖资格", "空目标资格", "读压力可用")):
                raise ValueError("候选容量或资格非法")
            if c["读压力可用"] and t <= 0: raise ValueError("有效遥测缺少阈值")
            space = (cap-v)*1000000//cap
            risk = min(r, t)*1000000//t if c["读压力可用"] else 0
            score = (space*space_weight+risk*(100-space_weight))//100
            if (space, risk, score) != tuple(c[k] for k in ("空间分量ppm", "风险分量ppm", score_key)):
                raise ValueError("原始候选归一化打分不符")
            pending.append(c)
        if "简单双因素实际集合：" in line:
            fields, previous = {}, None
            for part in line.split("简单双因素实际集合：", 1)[1].split("，"):
                if "=" in part:
                    previous, value = part.split("=", 1)
                    fields[previous] = value
                elif previous is not None:
                    # 状态正文自身含中文逗号，不可当成新的键值字段。
                    fields[previous] += "，"+part
                else:
                    raise ValueError("实际集合字段非法")
            if fields["模式"] != mode or int(fields["原合格区"]) != len(pending): raise ValueError("实际集合模式或候选数量不符")
            eligible = [c for c in pending if c["容量字节"]-c["有效字节"] >= budget and c["覆盖资格"] and c["空目标资格"]]
            if eligible:
                weighted = mode == "weighted" and all(c["读压力可用"] and c["读数阈值"] for c in eligible)
                expected = min(eligible, key=lambda c: (-c[score_key] if weighted else 0, c["有效字节"], c["Zone"]))
                if (int(fields["实际选中"]), int(fields["Zone"]), int(fields["回收预算"]), int(fields[used_key]), int(fields[score_key])) != \
                        (1, expected["Zone"], budget, int(weighted), expected[score_key]):
                    raise ValueError("真实选区不符合简单双因素排序")
                decisions.append({"行号": line_no, "原空间合格区数": len(pending), "可排序候选数": len(eligible),
                                  used_key: weighted, "实际选择": expected})
            else:
                possible = any(c["容量字节"]-c["有效字节"] >= budget for c in pending)
                expected_count = 0 if possible else len(pending)
                expected_status = "覆盖或目标未就绪" if possible else "单区预算不可满足，退回原批量策略"
                if int(fields["实际选中"]) != expected_count or fields["状态"] != expected_status:
                    raise ValueError("等待或批量回退集合不符")
                if not pending: empty_rounds += 1
                elif not possible: fallbacks += 1
            pending = []
    if pending: raise ValueError("候选缺少最终实际集合")
    return {"成功单区决策": decisions, "非空实际批量回退次数": fallbacks, "零候选空集合轮数": empty_rounds}


def periodic_stability(windows, ends):
    """事先确定的筛查：至少4个完整计量周期，空间与维护量没有明显周期漂移。

    指标只在相同业务相位比较；不宣称80秒内负载恒定或是真实DB稳态。
    """
    checks = {
        "至少四个预热后完整周期": len(windows) >= 4,
        "边界有效量波动不超过32MiB": bool(ends) and max(e["有效字节"] for e in ends)-min(e["有效字节"] for e in ends) <= 32*MIB,
        "边界空闲量波动不超过一个Zone": bool(ends) and max(e["空闲字节"] for e in ends)-min(e["空闲字节"] for e in ends) <= 256*MIB,
        "边界满区回收量波动不超过一个Zone": bool(ends) and max(e["满区可回收字节"] for e in ends)-min(e["满区可回收字节"] for e in ends) <= 256*MIB,
    }
    for key in ("ZC次数", "RR完成次数", "总维护迁移字节"):
        checks[f"每周期{key}变异系数不超过0.25"] = bool(windows) and cv([w[key] for w in windows]) <= .25
    checks["存在重复的有搬运维护事件"] = bool(windows) and sum(w["ZC次数"]+w["RR完成次数"] for w in windows) >= 4
    return {"结论": "达到本负载的相对周期稳定筛查" if all(checks.values()) else "尚未达到相对周期稳定筛查",
            "筛查": checks, "限制": "固定相位周期轮换的合成ZenFS负载，不是持续随机负载或RocksDB数据库稳态"}


def analyze(run):
    for file in ("mkfs.exit", "run.exit", "verify.exit", "runner.exit"):
        if (run/file).read_text().strip() != "0":
            raise ValueError(f"{run}: {file}未成功")
    identity = (run/"identity.initial.sha256").read_text()
    if identity != (run/"identity.final.sha256").read_text():
        raise ValueError("软件身份运行前后改变")
    for line in (run/"logs.sha256").read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        relative = Path(filename).relative_to(Path("/tmp")/run.name)
        if hashlib.sha256((run/relative).read_bytes()).hexdigest() != digest:
            raise ValueError(f"原始记录哈希不符：{relative}")
    config = rows(run/"config.csv")
    if len(config) != 1 or config[0]["策略"] not in ("valid", "weighted") or int(config[0]["周期秒数"]) != PERIOD or int(config[0]["预热周期数"]) != WARMUP:
        raise ValueError("实验配置不符")
    cycles = int(config[0]["周期数"])
    text = (run/"zenfs.run.log").read_text()
    lines = text.splitlines()
    parsed = parse_lines(selection_view(lines), 64)
    decisions = validate_decisions(lines, config[0]["策略"], int(config[0]["回收预算字节"]))
    if "[ERROR]" in text or parsed["failed_resets"] or any(e["status"] != "完成" for e in parsed["rr"]):
        raise ValueError("存在维护错误或不完整RR，不能作为成功完整对照")
    if set(re.findall(r"GC 检查：.*启动水平=(\d+)%", text)) != {"20"}:
        raise ValueError("GC20%未确认")
    verify = (run/"zenfs.verify.log").read_text()
    if "[ERROR]" in verify or "RR 开始：" in verify or ZC_STATS.search(verify) or "608MiB完整字节比对" not in (run/"verify.stdout.log").read_text():
        raise ValueError("独立最终校验未通过或引入额外维护")
    anchors = []
    for line in lines:
        prefix = PREFIX.match(line)
        marker = MARKER.search(line)
        if prefix and marker:
            anchors.append(datetime.strptime(prefix[1], "%Y/%m/%d-%H:%M:%S.%f").timestamp()-int(marker[3])/1e9)
    if not anchors or max(anchors)-min(anchors) > .020:
        raise ValueError("日志时间与业务单调时钟缺少稳定对齐")
    anchor = mean(anchors)
    def index(line):
        prefix = PREFIX.match(line)
        if not prefix:
            raise ValueError("维护事件缺少时间戳")
        return math.floor((datetime.strptime(prefix[1], "%Y/%m/%d-%H:%M:%S.%f").timestamp()-anchor)/PERIOD)
    windows = [{"周期": c, "GC检查次数": 0, "ZC次数": 0, "ZC迁移字节": 0, "ZC重置区数": 0,
                "RR完成次数": 0, "RR迁移字节": 0, "RR重置区数": 0} for c in range(cycles)]
    zc_events, rr_events = [], []
    outside = []
    for line_no, line in enumerate(lines, 1):
        match = ZC_STATS.search(line)
        if match:
            round_no, moved, resets = map(int, match.groups())
            zc_events.append({"行号": line_no, "周期": index(line), "轮次": round_no, "迁移字节": moved, "重置区数": resets})
        if "GC 检查：" in line:
            c = index(line)
            if 0 <= c < cycles: windows[c]["GC检查次数"] += 1
    for e in parsed["rr"]:
        rr_events.append({"行号": e["line"], "结束行号": e["end_line"], "周期": index(lines[e["end_line"]-1]),
                          "Zone": e["zone"], "迁移字节": e["moved"], "重置区数": e["resets"]})
    for kind, events in (("ZC", zc_events), ("RR", rr_events)):
        for e in events:
            c = e["周期"]
            if not 0 <= c < cycles:
                outside.append(dict(e, 类型=kind))
                continue
            w = windows[c]
            w[kind+"迁移字节"] += e["迁移字节"]
            w[kind+"重置区数"] += e["重置区数"]
            w["ZC次数" if kind == "ZC" else "RR完成次数"] += int(kind == "RR" or e["迁移字节"] > 0)
    operations = rows(run/"operations.csv")
    trace = rows(run/"actual-trace.csv")
    if len(operations) != len(trace): raise ValueError("操作和实际轨迹长度不符")
    counters = Counter()
    values, by_phase, delays, window_values = defaultdict(list), defaultdict(list), [], defaultdict(lambda: defaultdict(list))
    for r, t in zip(operations, trace):
        if {k: r[k] for k in t} != t or r["成功"] != "1": raise ValueError("实际轨迹不符或存在失败请求")
        c, duration = int(r["周期"]), int(r["延迟纳秒"])
        if c >= 0:
            if c >= cycles: raise ValueError("操作周期越界")
            counters[(c, r["阶段"], r["操作"])] += 1
            if r["操作"] in ("读4KiB", "写1MiB"):
                window_values[c][r["操作"]].append(duration)
        if c >= WARMUP:
            values[r["操作"]].append(duration)
            by_phase[(r["阶段"], r["操作"])].append(duration)
            if r["操作"] in ("读4KiB", "写1MiB"):
                delays.append(int(r["排程迟到纳秒"]))
    for c in range(cycles):
        for phase, count in (("阈值以下读热身", 7168), ("后续业务读", 1536)):
            if counters[(c, phase, "读4KiB")] != count: raise ValueError("业务读取未完成固定计划")
        if counters[(c, "新代写入", "写1MiB")] != 512 or counters[(c, "后续业务写", "写1MiB")] != 512:
            raise ValueError("业务写入未完成固定计划")
        windows[c]["总维护迁移字节"] = windows[c]["ZC迁移字节"]+windows[c]["RR迁移字节"]
        windows[c]["业务读字节"] = (7168+1536)*4096
        windows[c]["业务写字节"] = 1024*MIB
    ends = [{k: int(r[k]) for k in ("周期", "空闲字节", "有效字节", "满区可回收字节", "累计IO区成功重置")}
            for r in rows(run/"states.csv") if r["阶段"] == "周期结束"]
    if [e["周期"] for e in ends] != list(range(cycles)): raise ValueError("周期结束空间记录不完整")
    # 维护总量求和时不混入延迟字典；各周期延迟单独输出以观察漂移。
    measured = windows[WARMUP:]
    totals = {"ZC次数": sum(e["迁移字节"] > 0 for e in zc_events), "RR完成次数": len(rr_events),
              "ZC迁移字节": sum(e["迁移字节"] for e in zc_events), "RR迁移字节": sum(e["迁移字节"] for e in rr_events),
              "ZC重置区数": sum(e["重置区数"] for e in zc_events), "RR重置区数": sum(e["重置区数"] for e in rr_events)}
    totals["总维护迁移字节"] = totals["ZC迁移字节"]+totals["RR迁移字节"]
    # 业务DeleteFile造成的自然全无效回收不是带搬运的ZC，也不是独立RR。
    reset_zones = list(map(int, re.findall(r"Zone 重置诊断：Zone=(\d+)，状态=成功，", text)))
    # 前三个区属于ZenFS元数据，不可混入IO数据区的自然删除回收。
    all_resets = sum(z >= 3*256*MIB for z in reset_zones)
    totals["元数据区成功重置次数"] = len(reset_zones)-all_resets
    totals["所有主机IO区成功重置次数"] = all_resets
    totals["非ZC非RR成功重置次数"] = all_resets-totals["ZC重置区数"]-totals["RR重置区数"]
    if totals["非ZC非RR成功重置次数"] < 0: raise ValueError("重置总账小于维护源重置")
    return {"来源": str(run), "配置": config[0], "软件身份": identity, "实际轨迹sha256": hashlib.sha256((run/"actual-trace.csv").read_bytes()).hexdigest(),
            "全程维护": totals, "预热后维护": {k: sum(w[k] for w in measured) for k in windows[0] if k != "周期"},
            "每周期": windows, "周期边界空间": ends, "相对周期稳定": periodic_stability(measured, ends[WARMUP:]),
            "每周期API延迟": [{"周期": c, **{k: latency(v) for k, v in window_values[c].items()}} for c in range(cycles)],
            "预热后API延迟": {k: latency(v) for k, v in values.items()},
            "预热后分阶段API延迟": {f"{p}/{k}": latency(v) for (p, k), v in by_phase.items()},
            "预热后排程迟到": latency(delays), "ZC事件": zc_events, "RR事件": rr_events, "计时窗口外维护": outside,
            "逐轮评分与实际选区复核": decisions,
            "终态IO区形态": zone_shape(json.loads((run/"zones.final.json").read_text())["zone_list"]),
            "延迟口径": "真实同步ZenFS API耗时，排程等待单列；不是设备NAND延迟、开放队列端到端延迟或RocksDB请求延迟"}


def validate_pair(runs):
    if len(runs) != 2 or {r["配置"]["策略"] for r in runs} != {"valid", "weighted"}:
        raise ValueError("需要完整的两种策略各一组")
    a, b = runs
    for key in ("软件身份", "实际轨迹sha256"):
        if a[key] != b[key]: raise ValueError(f"两组{key}不同")
    if {k: v for k, v in a["配置"].items() if k != "策略"} != {k: v for k, v in b["配置"].items() if k != "策略"}:
        raise ValueError("两组计划参数不同")
    # 不要求不同策略终态必须相等；不相等时显式保留未偿清的空间账目。
    return {"相同程序与实际逻辑轨迹": True,
            "终态空间一致": [{k: v for k, v in r["周期边界空间"][-1].items() if k not in ("累计IO区成功重置",)} for r in runs][0] ==
                             [{k: v for k, v in r["周期边界空间"][-1].items() if k not in ("累计IO区成功重置",)} for r in runs][1],
            "说明": "仅串行单次配对，未完成多种负载或独立重复；相对周期稳定不等于通用稳态收益"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    args = parser.parse_args()
    result = {"运行": [analyze(p) for p in args.runs]}
    if len(result["运行"]) == 2: result["配对核验"] = validate_pair(result["运行"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
