#!/usr/bin/env python3
"""只读分析完整主日志；地址关联不等于同代数据或可避免的 RR 成本。"""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


PREFIX = re.compile(r"^(\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}\.\d{6}) (\d+) (.*)$")
CANDIDATE = re.compile(
    r"ZC 候选：Zone (\d+)，无效比例 (\d+)%，有效数据 (\d+) 字节，"
    r"(?:RR读数阈值 (\d+)，最大读数 (\d+)，读数总和 (\d+)，"
    r"风险块 80%/90%/100%=(\d+)/(\d+)/(\d+)|读压力未知)，选中 ([01])$"
)
GC_SUMMARY = re.compile(
    r"GC 选区汇总：检查空闲比例=(\d+)%，无效比例门槛=(\d+)%，"
    r"满Zone=(\d+)，最大无效比例=\d+%，选中Zone=(\d+)，"
)
RR_START = re.compile(
    r"RR 开始：Zone (\d+)，达阈值物理块 (\d+)，有效数据 (\d+) 字节，"
    r"封闭剩余容量 (\d+) 字节$"
)
RR_STATS = re.compile(
    r"RR 统计：尝试 (\d+) 次，本次迁移 (\d+) 字节，本次重置 (\d+) 个 Zone，"
    r"累计迁移 (\d+) 字节$"
)
RR_DONE = re.compile(r"RR 完成：Zone (\d+) 已重置，累计完成 (\d+) 次$")
EMPTY_ALLOC = re.compile(
    r"Allocating zone\(new=1\) start: 0x([0-9a-f]+) wp: 0x([0-9a-f]+)"
)
OBSERVATION = re.compile(
    r"Zone 维护观测：阶段=(ZC候选|RR扫描|RR开始)，Zone=(\d+)，"
    r"主机代际=(\d+)/(\d+)/(\d+)，观察序号=(\d+)/(\d+)/(\d+)，"
    r"重置窗稳定=([01])，快照写指针=(\d+)，满区=([01])，"
    r"有效字节=(\d+)，覆盖字节=(\d+)，含非SST=([01])，覆盖完整=([01])，"
    r"SST覆盖资格=([01])，读压力可用=([01])，达阈值块=(\d+)，迁移准入=未检查$"
)


def parse_observation(message, base, blocks_per_zone):
    """校验诊断自身的一致性；稳定仅指本进程 Reset，不代表原子数据快照。"""
    match = OBSERVATION.fullmatch(message)
    if not match:
        raise ValueError(f"第 {base['line']} 行维护观测格式无法解析")
    stage, *numbers = match.groups()
    keys = ("zone", "generation_before", "generation_after", "generation_current",
            "sequence_before", "sequence_after", "sequence_current", "stable", "wp",
            "full", "valid", "covered", "non_sst", "coverage_complete", "sst_eligible",
            "pressure_available", "at")
    item = dict(base, stage=stage, **dict(zip(keys, map(int, numbers))))
    if item["stable"] and (len({item[f"generation_{s}"] for s in ("before", "after", "current")}) != 1
                           or len({item[f"sequence_{s}"] for s in ("before", "after", "current")}) != 1
                           or item["sequence_current"] % 2):
        raise ValueError(f"第 {base['line']} 行稳定标记与代际/序号不符")
    covered = int(item["covered"] == item["valid"])
    eligible = int(item["valid"] > 0 and item["wp"] != item["zone"] and covered and not item["non_sst"])
    if item["coverage_complete"] != covered or item["sst_eligible"] != eligible:
        raise ValueError(f"第 {base['line']} 行 SST 覆盖资格与诊断字段不符")
    if not 0 <= item["at"] <= blocks_per_zone or (not item["pressure_available"] and item["at"]):
        raise ValueError(f"第 {base['line']} 行维护观测读压力不一致")
    return item


def parse_lines(lines, blocks_per_zone):
    """保留顺序、线程和行号；拒绝不完整的候选批次及不配对的 RR 记录。"""
    candidates, batches, rr_events, barriers, deferred = [], [], [], [], []
    observations, failed_resets, diagnostic_pending = [], [], {}
    pending, active = [], {}
    for line_no, line in enumerate(lines, 1):
        prefix = PREFIX.match(line.rstrip())
        if not prefix:
            continue
        time, thread, message = prefix.groups()
        base = {"time": time, "line": line_no}
        if "Zone 维护观测：" in message:
            item = parse_observation(message, base, blocks_per_zone)
            observations.append(item)
            if item["stage"] != "RR扫描":
                key = (thread, item["stage"], item["zone"])
                if key in diagnostic_pending:
                    raise ValueError(f"第 {line_no} 行诊断缺少对应执行记录")
                diagnostic_pending[key] = item
        elif "ZC 候选：" in message:
            match = CANDIDATE.fullmatch(message)
            if not match:
                raise ValueError(f"第 {line_no} 行候选格式无法解析")
            values = [int(v) if v is not None else None for v in match.groups()]
            zone, invalid, valid, threshold, maximum, total, ge80, ge90, at, selected = values
            if at is not None and not (0 <= at <= ge90 <= ge80 <= blocks_per_zone):
                raise ValueError(f"第 {line_no} 行风险块数与几何参数不符")
            item = dict(base, zone=zone, invalid=invalid, valid=valid,
                        threshold=threshold, maximum=maximum, total=total,
                        ge80=ge80, ge90=ge90, at=at, selected=selected)
            item["diagnostic"] = diagnostic_pending.pop((thread, "ZC候选", zone), None)
            diagnostic = item["diagnostic"]
            if diagnostic and (diagnostic["valid"] != valid or not diagnostic["full"]
                               or diagnostic["pressure_available"] != int(at is not None)
                               or (at is not None and diagnostic["at"] != at)):
                raise ValueError(f"第 {line_no} 行候选与维护观测不一致")
            candidates.append(item)
            pending.append(item)
        elif "GC 选区汇总：" in message:
            match = GC_SUMMARY.match(message)
            if not match:
                raise ValueError(f"第 {line_no} 行选区汇总格式无法解析")
            free, gate, full, selected = map(int, match.groups())
            if len(pending) != full or sum(c["selected"] for c in pending) != selected:
                raise ValueError(f"第 {line_no} 行汇总与候选批次不一致")
            for item in pending:
                if item["selected"] != int(gate < item["invalid"] < 100):
                    raise ValueError(f"第 {item['line']} 行不符合当前空间门槛")
                item.update(batch=len(batches) + 1, free=free, gate=gate)
            batches.append(dict(base, count=full, selected=selected))
            pending = []
        elif "RR 开始：" in message:
            match = RR_START.fullmatch(message)
            if not match or thread in active:
                raise ValueError(f"第 {line_no} 行 RR 开始记录不完整或未配对")
            zone, at, valid, tail = map(int, match.groups())
            item = dict(base, zone=zone, at=at, valid=valid, tail=tail,
                        attempt=None, moved=None, resets=None, status="未确认",
                        error="", end_line=None)
            item["diagnostic"] = diagnostic_pending.pop((thread, "RR开始", zone), None)
            diagnostic = item["diagnostic"]
            if diagnostic and (diagnostic["valid"] != valid or diagnostic["at"] != at
                               or not diagnostic["pressure_available"] or not diagnostic["sst_eligible"]):
                raise ValueError(f"第 {line_no} 行 RR 开始与维护观测不一致")
            rr_events.append(item)
            active[thread] = item
        elif "RR 统计：" in message:
            match = RR_STATS.fullmatch(message)
            if not match or thread not in active:
                raise ValueError(f"第 {line_no} 行 RR 统计没有匹配的开始记录")
            attempt, moved, resets, cumulative = map(int, match.groups())
            item = active[thread]
            if item["attempt"] is not None:
                raise ValueError(f"第 {line_no} 行 RR 统计重复")
            item.update(attempt=attempt, moved=moved, resets=resets,
                        cumulative=cumulative, end_line=line_no)
        elif "RR 完成：" in message:
            match = RR_DONE.fullmatch(message)
            if not match or thread not in active:
                raise ValueError(f"第 {line_no} 行 RR 完成没有匹配的开始记录")
            item = active.pop(thread)
            if int(match[1]) != item["zone"] or item["resets"] != 1:
                raise ValueError(f"第 {line_no} 行 RR 完成的源区或重置数不符")
            item.update(status="完成", end_line=line_no)
            barriers.append(dict(base, zone=item["zone"], reason="RR 完成并重置"))
        elif "主机端 RR 检查失败：" in message and thread in active:
            item = active.pop(thread)
            item.update(status="中止", end_line=line_no,
                        error=message.split("主机端 RR 检查失败：", 1)[1])
        elif "RR 未完成：" in message:
            item = active.pop(thread)
            item.update(status="未完成", end_line=line_no)
        else:
            match = re.search(r"Zone 重置诊断：Zone=(\d+)，状态=失败，", message)
            if match:
                failed_resets.append(dict(base, zone=int(match[1]), reason="Reset 失败，介质代际不确定"))
            match = re.search(r"Zone 重置诊断：Zone=(\d+)，状态=成功，主机代际=(\d+)/(\d+)，", message)
            if match:
                zone, previous, current = map(int, match.groups())
                if current != previous + 1:
                    raise ValueError(f"第 {line_no} 行成功 Reset 代际没有递增")
                barriers.append(dict(base, zone=zone,
                                     reason=f"主机成功 Reset：代际 {previous}/{current}"))
            match = EMPTY_ALLOC.search(message)
            if match and match[1] == match[2]:
                barriers.append(dict(base, zone=int(match[1], 16), reason="重新取得普通空区"))
            match = re.search(r"迁移预留取得空区：Zone=(\d+)$", message)
            if match:
                barriers.append(dict(base, zone=int(match[1]), reason="取得空预留区"))
            match = re.search(r"迁移预留替换：旧Zone=\d+，新Zone=(\d+)$", message)
            if match:
                barriers.append(dict(base, zone=int(match[1]), reason="空替补接管预留"))
            match = re.search(r"RR 延后：Zone (\d+) ", message)
            if match:
                deferred.append(dict(base, zone=int(match[1])))
    if pending:
        raise ValueError("候选批次缺少选区汇总；请使用完整主日志")
    if active:
        raise ValueError("RR 尝试缺少最终状态；请使用完整主日志")
    if diagnostic_pending or (observations and any(not item["diagnostic"] for item in candidates + rr_events)):
        raise ValueError("维护观测与候选/RR 开始未完整配对；请使用完整主日志")
    return dict(candidates=candidates, batches=batches, rr=rr_events,
                barriers=barriers, deferred=deferred, observations=observations, failed_resets=failed_resets)


def make_relation(candidate, following, barriers, failed_resets=()):
    cuts, delta = [], None
    status = "未观察到后续同地址 RR（窗口结束，不代表不会发生）"
    if candidate is None:
        status = "此前未观察到同地址满区候选"
    elif following:
        cuts = [b for b in barriers if b["zone"] == candidate["zone"]
                and candidate["line"] < b["line"] <= following["line"]]
        status = "跨重置或空区复用，排除" if cuts else "未见复用证据，代际尚未完整确认"
        failures = [r for r in failed_resets if r["zone"] == candidate["zone"]
                    and candidate["line"] < r["line"] <= following["line"]]
        first, last = candidate.get("diagnostic"), following.get("diagnostic")
        if not cuts and failures:
            status = "期间 Reset 失败，代际无法确认"
        elif not cuts and first and last:
            if not first["stable"] or not last["stable"]:
                status = "端点重置窗不稳定，代际无法确认"
            elif (first["generation_current"], first["sequence_current"]) != (last["generation_current"], last["sequence_current"]):
                status = "端点代际或序号不同，排除"
            else:
                status = "同一主机 Reset 代际（非原子数据快照）"
        delta = (datetime.strptime(following["time"], "%Y/%m/%d-%H:%M:%S.%f")
                 - datetime.strptime(candidate["time"], "%Y/%m/%d-%H:%M:%S.%f")).total_seconds()
    return dict(candidate=candidate, rr=following, cuts=cuts, status=status, delta=delta)


def relate_candidates(parsed):
    """每个观测只关联最近的后续同地址 RR，避免一个快照重复计费。"""
    return [make_relation(c, next((rr for rr in parsed["rr"] if rr["zone"] == c["zone"]
            and rr["line"] > c["line"]), None), parsed["barriers"], parsed["failed_resets"]) for c in parsed["candidates"]]


def relate_reclaims(parsed):
    """反向检查每次 RR 的最近此前同地址观测，展示跨代误关联。"""
    return [make_relation(next((c for c in reversed(parsed["candidates"])
            if c["zone"] == rr["zone"] and c["line"] < rr["line"]), None),
            rr, parsed["barriers"], parsed["failed_resets"]) for rr in parsed["rr"]]


def analyze(path, blocks_per_zone):
    data = path.read_bytes()
    parsed = parse_lines(data.decode("utf-8").splitlines(), blocks_per_zone)
    relations = relate_candidates(parsed)
    rr_relations = relate_reclaims(parsed)
    known = [c for c in parsed["candidates"] if c["at"] is not None]
    linked = [r for r in relations if r["rr"] is not None]
    uncut = [r for r in linked if not r["cuts"]]
    summary = {
        "来源": str(path), "来源哈希": hashlib.sha256(data).hexdigest(),
        "每区物理块数（显式输入）": blocks_per_zone,
        "选区快照数": len(parsed["batches"]), "满区观测行数": len(parsed["candidates"]),
        "读压力可用行数": len(known), "全部块达阈值行数": sum(c["at"] == blocks_per_zone for c in known),
        "空间门槛选中观测数": sum(c["selected"] for c in parsed["candidates"]),
        "RR尝试数": len(parsed["rr"]), "RR完成数": sum(r["status"] == "完成" for r in parsed["rr"]),
        "RR中止数": sum(r["status"] == "中止" for r in parsed["rr"]),
        "候选后同地址RR关联行数": len(linked), "跨复用排除行数": sum(bool(r["cuts"]) for r in linked),
        "未见复用证据关联行数": len(uncut),
        "未见复用证据关联RR尝试数": len({r["rr"]["line"] for r in uncut}),
        "未见复用证据且空间门槛选中行数": sum(r["candidate"]["selected"] for r in uncut),
        "RR此前有同地址满区观测的尝试数": sum(r["candidate"] is not None for r in rr_relations),
        "RR反向关联跨复用排除数": sum(bool(r["cuts"]) for r in rr_relations),
        "RR源区尝试分布": dict(sorted(Counter(r["zone"] for r in parsed["rr"]).items())),
        "RR延后区观测次数": dict(sorted(Counter(r["zone"] for r in parsed["deferred"]).items())),
        "维护观测行数": len(parsed["observations"]),
        "维护观测重置窗不稳定行数": sum(not o["stable"] for o in parsed["observations"]),
        "Reset失败行数": len(parsed["failed_resets"]),
        "同一主机代际候选关联行数": sum(r["status"].startswith("同一主机") for r in relations),
        "同一主机代际关联RR尝试数": len({r["rr"]["line"] for r in relations if r["status"].startswith("同一主机")}),
    }
    return dict(summary=summary, parsed=parsed, relations=relations, rr_relations=rr_relations)


def write_reports(output, runs):
    """输出到新目录，不覆盖已有分析或原始日志。"""
    if output.exists():
        raise ValueError(f"输出目录已存在，停止以免覆盖：{output}")
    output.mkdir(parents=True)
    notes = ["满区观测不等于满足原空间门槛或 RR 的 SST/快照覆盖资格。",
             "同地址关联及未见复用证据不证明同代数据，不计算可避免成本。",
             "观测行可能重复同一区，未观察到后续 RR 是有限窗口内的结果。",
             "新诊断只记录本进程 Reset；不持久化，不覆盖外部 Reset，不能证明数据快照原子性。",
             "旧日志缺少诊断时保持代际未确认；SST 覆盖资格不含目标容量或运行时占用准入。",
             "日志不能恢复完整逐块读数或物理块有效数据分布，不计算可避免成本。"]
    (output / "观测汇总.json").write_text(json.dumps(
        {"口径限制": notes, "日志汇总": [r["summary"] for r in runs]},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output / "候选关联.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["来源", "快照", "候选行号", "候选时间（客机UTC）", "Zone字节地址",
                         "整数空闲比例", "原无效比例门槛", "近似无效比例", "有效字节", "原策略选中",
                         "RR阈值", "最大读数", "读数总和", "80%风险块", "90%风险块", "达阈值块",
                         "后续RR尝试", "RR开始行号", "RR开始时间（客机UTC）", "间隔秒",
                         "RR实际迁移字节", "RR最终状态", "关联口径", "复用证据行号及原因",
                         "候选诊断行号", "候选主机代际", "候选观察序号", "候选重置窗稳定",
                         "候选SST覆盖资格（非迁移准入）", "RR诊断行号", "RR主机代际", "RR观察序号", "RR重置窗稳定"])
        for run in runs:
            for relation in run["relations"]:
                c, rr = relation["candidate"], relation["rr"] or {}
                first, last = c.get("diagnostic") or {}, rr.get("diagnostic") or {}
                writer.writerow([run["summary"]["来源"], c["batch"], c["line"], c["time"], c["zone"],
                                 c["free"], c["gate"], c["invalid"], c["valid"], c["selected"],
                                 c["threshold"], c["maximum"], c["total"], c["ge80"], c["ge90"], c["at"],
                                 rr.get("attempt"), rr.get("line"), rr.get("time"), relation["delta"],
                                 rr.get("moved"), rr.get("status"), relation["status"],
                                 "；".join(f"{b['line']}：{b['reason']}" for b in relation["cuts"]),
                                 first.get("line"), first.get("generation_current"), first.get("sequence_current"),
                                 first.get("stable"), first.get("sst_eligible"), last.get("line"),
                                 last.get("generation_current"), last.get("sequence_current"), last.get("stable")])
    with (output / "RR事件.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["来源", "RR尝试", "Zone字节地址", "开始行号", "开始时间（客机UTC）",
                         "达阈值块", "快照有效字节", "牺牲尾部字节", "实际迁移字节", "重置数",
                         "最终状态", "结束行号", "中止错误", "最近此前同地址候选行号",
                         "关联口径", "复用证据行号及原因"])
        for run in runs:
            for relation in run["rr_relations"]:
                rr, candidate = relation["rr"], relation["candidate"] or {}
                writer.writerow([run["summary"]["来源"], rr["attempt"], rr["zone"], rr["line"], rr["time"],
                                 rr["at"], rr["valid"], rr["tail"], rr["moved"], rr["resets"],
                                 rr["status"], rr["end_line"], rr["error"], candidate.get("line"),
                                 relation["status"], "；".join(f"{b['line']}：{b['reason']}" for b in relation["cuts"])])
    with (output / "维护观测.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["来源", "行号", "时间（客机UTC）", "阶段", "Zone字节地址",
                         "主机代际（前/后/当前）", "观察序号（前/后/当前）", "重置窗稳定",
                         "快照写指针", "满区", "有效字节", "覆盖字节", "含非SST", "覆盖完整",
                         "SST覆盖资格（非迁移准入）", "读压力可用", "达阈值块"])
        for run in runs:
            for o in run["parsed"]["observations"]:
                writer.writerow([run["summary"]["来源"], o["line"], o["time"], o["stage"], o["zone"],
                                 "/".join(str(o[f"generation_{s}"]) for s in ("before", "after", "current")),
                                 "/".join(str(o[f"sequence_{s}"]) for s in ("before", "after", "current")),
                                 o["stable"], o["wp"], o["full"], o["valid"], o["covered"], o["non_sst"],
                                 o["coverage_complete"], o["sst_eligible"], o["pressure_available"], o["at"]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", type=Path, nargs="+", help="完整主进程 ZenFS 日志；不要混入后校验")
    parser.add_argument("--blocks-per-zone", type=int, required=True, help="依据实际设备配置填写每区物理块数")
    parser.add_argument("--output-dir", type=Path, required=True, help="尚不存在的新分析目录")
    args = parser.parse_args()
    if args.blocks_per_zone <= 0 or len(set(args.logs)) != len(args.logs):
        parser.error("物理块数必须为正，输入日志不得重复")
    try:
        runs = [analyze(path, args.blocks_per_zone) for path in args.logs]
        write_reports(args.output_dir, runs)
    except (OSError, UnicodeError, ValueError, KeyError) as error:
        parser.exit(1, f"分析失败：{error}\n")
    for run in runs:
        print(json.dumps(run["summary"], ensure_ascii=False))
    print(f"分析已保存：{args.output_dir}；地址关联不代表已证明 RR 可避免。")


if __name__ == "__main__":
    main()
