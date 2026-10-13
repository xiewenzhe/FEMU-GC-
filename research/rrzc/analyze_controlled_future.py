#!/usr/bin/env python3
"""核验固定未来读取窗口，区分采样跨阈值、实际RR及迁移后的再次维护。"""
import argparse
import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from analyze_controlled_near import validate_initial
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import PREFIX, parse_lines


def read_csv(path):
    with path.open() as handle:
        return [{k: int(v) for k, v in row.items()} for row in csv.DictReader(handle)]


def validate_samples(rows):
    """稳定只指本进程Reset窗口；不认为读压力与文件映射是原子快照。"""
    if not rows:
        raise ValueError("缺少读压力采样")
    seen, last_ms = set(), -1
    crossings = []
    for row in rows:
        ms = row["窗口毫秒"]
        if ms < last_ms or row["最大读数"] > row["读数总和"]:
            raise ValueError("采样时间倒退或最大读数大于总和")
        last_ms = ms
        if not 0 <= row["达阈值块"] <= row["90%风险块"] <= row["80%风险块"] <= 64:
            raise ValueError("风险块数量不符合64块几何")
        stable = row["重置窗稳定"]
        if stable and (row["主机代际前"] != row["主机代际后"]
                       or row["观察序号前"] != row["观察序号后"] or row["观察序号后"] % 2):
            raise ValueError("稳定标记与Reset窗口不一致")
        if row["首次采样跨阈值"]:
            key = row["Zone"], row["主机代际后"]
            if not stable or row["最大读数"] < 4096 or row["达阈值块"] == 0 or key in seen:
                raise ValueError("首次跨阈值记录不稳定、未达阈值或同代重复")
            seen.add(key)
            crossings.append(row)
    return crossings


def validate_counts(rows, rate, read_seconds, window_seconds):
    if not rows:
        raise ValueError("缺少实际业务读次数")
    final, last = {}, {}
    for row in rows:
        i, ms, count = row["组号"], row["窗口毫秒"], row["累计业务4KiB读次数"]
        if i not in range(4) or count < 0 or count > rate * read_seconds:
            raise ValueError("业务次数超出计划或组号错误")
        if i in last and (ms < last[i][0] or count < last[i][1]):
            raise ValueError("业务读时间或次数倒退")
        last[i] = ms, count
        final[i] = count
    if set(final) != set(range(4)) or len(set(final.values())) != 1:
        raise ValueError("实际业务读取四组未完成相同轮次")
    if min(ms for ms, _ in last.values()) < window_seconds * 1000:
        raise ValueError("观察窗口未完成")
    return final


def get_marker(lines, phrase):
    found = [(i, PREFIX.match(line)) for i, line in enumerate(lines, 1) if "未来读取观察：" in line and phrase in line]
    if len(found) != 1 or not found[0][1]:
        raise ValueError(f"窗口边界缺失或重复：{phrase}")
    index, match = found[0]
    return index, datetime.strptime(match[1], "%Y/%m/%d-%H:%M:%S.%f")


def describe_rr(event, lines, start_time, close_line, crossing_markers):
    diag = event.get("diagnostic") or {}
    if event["status"] != "完成" or event["resets"] != 1 or not diag.get("stable") or not diag.get("sst_eligible"):
        raise ValueError("RR未完成或缺少稳定覆盖资格")
    names = re.findall(r"MigrateFileExtents, fname: (/controlled-zc/case([0-3])\.live\.sst),", "\n".join(lines[event["line"] - 1:event["end_line"]]))
    indices = [int(i) for _, i in names]
    if not indices or len(indices) != len(set(indices)):
        raise ValueError("RR缺少唯一的实际迁移文件记录")
    if event["moved"] != sum((32 if i < 2 else 64) << 20 for i in indices) or event["moved"] != event["valid"]:
        raise ValueError("RR实际迁移文件和字节统计不符")
    start = datetime.strptime(event["time"], "%Y/%m/%d-%H:%M:%S.%f")
    matching = [m for m in crossing_markers if m["zone"] == event["zone"]
                and m["generation"] == diag["generation_current"] and m["line"] < event["line"]]
    cross = matching[0] if matching else None
    return {"源Zone": event["zone"], "主机Reset代际": diag["generation_current"],
            "RR开始窗口秒": (start - start_time).total_seconds(), "开始行号": event["line"], "结束行号": event["end_line"],
            "开始在关闭请求之前": event["line"] < close_line, "达阈值块": event["at"],
            "实际迁移字节": event["moved"], "牺牲尾部字节": event["tail"], "实际迁移文件组号": indices,
            "首次采样跨阈值窗口毫秒": cross["ms"] if cross else None,
            "采样跨阈值至RR开始秒": (start - cross["time"]).total_seconds() if cross else None,
            "采样口径": "同一主机Reset代际，首次采样而非精确跨阈值时刻" if cross else "RR前未采样到同代跨阈值，不能补造时间"}


def analyze(run):
    plans = {"low": (8, 24), "medium": (32, 24), "high": (256, 24),
             "equal-slow": (32, 24), "equal-fast": (256, 3)}
    phases = [p for p in plans if (run / f"future-{p}.exit").exists()]
    if len(phases) != 1:
        raise ValueError("读取速率条件不明确")
    level = phases[0]
    for phase in ("mkfs", "runner", f"future-{level}", "near-verify"):
        if (run / f"{phase}.exit").read_text().strip() != "0":
            raise ValueError(f"未完成：{phase}")
    identity = (run / "identity.initial.sha256").read_bytes()
    if identity != (run / "identity.final.sha256").read_bytes() or len(identity.splitlines()) != 6:
        raise ValueError("软件身份变化")
    for line in (run / "logs.sha256").read_text().splitlines():
        digest, guest = line.split()
        if hashlib.sha256((run / Path(guest).name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"归档哈希不一致：{guest}")
    initial = list(csv.DictReader((run / "initial.csv").read_text().splitlines()))
    sources = [tuple(map(int, line.split())) for line in (run / "sources.tsv").read_text().splitlines()]
    validate_initial(initial, sources)
    configs = read_csv(run / "window.csv")
    rate, read_seconds = plans[level]
    if configs != [{"每文件目标读每秒": rate, "业务读取秒数": read_seconds, "观察窗口秒数": 40, "采样间隔毫秒": 100}]:
        raise ValueError("计划窗口配置不符")
    counts = validate_counts(read_csv(run / "read-counts.csv"), rate, read_seconds, 40)
    crossings = validate_samples(read_csv(run / "pressure.csv"))
    native = (run / f"zenfs.future-{level}.log").read_text()
    lines = native.splitlines()
    parsed = parse_lines(lines, 64)
    verify = (run / "zenfs.near-verify.log").read_text()
    vp = parse_lines(verify.splitlines(), 64)
    if "[ERROR]" in native + verify or parsed["failed_resets"] or vp["failed_resets"] or vp["rr"] or ZC_STATS.search(verify):
        raise ValueError("出现维护错误、失败Reset或校验阶段额外维护")
    start_line, start_time = get_marker(lines, "开始，每文件目标")
    close_line, _ = get_marker(lines, "窗口关闭请求")
    if start_line >= close_line or "无补读写锁" not in lines[start_line - 1]:
        raise ValueError("未来窗口未确认无补读写锁")
    deletion = [i for i, line in enumerate(lines, 1) if "受控近阈值：真实失效删除完成，时序=RR前" in line]
    if len(deletion) != 1 or deletion[0] >= start_line or len(re.findall(r"DeleteFile: /controlled-zc/case[23]\.expire\.sst", native)) != 2:
        raise ValueError("固定失效时序不在未来窗口之前")
    if set(re.findall(r"GC 检查：.*启动水平=(\d+)%", native)) != {"20"}:
        raise ValueError("未确认原GC20%")
    stats = [(i, tuple(map(int, m.groups()))) for i, line in enumerate(lines, 1) if (m := ZC_STATS.search(line))]
    prior = [s for i, s in stats if i < start_line]
    if len(prior) != 1 or prior[0][1:] != (64 << 20, 2) or any(e["line"] < start_line for e in parsed["rr"]):
        raise ValueError("未来窗口前初始维护条件不一致")
    for i, (zone, size) in enumerate(sources):
        if not any(c["zone"] == zone and c["valid"] == size and c["maximum"] == (448 if i % 2 else 3584)
                   and c["at"] == 0 and c["selected"] == int(i < 2) and c["free"] == 10 and c["gate"] == 70
                   and c["line"] < start_line and c.get("diagnostic") and c["diagnostic"]["sst_eligible"] for c in parsed["candidates"]):
            raise ValueError("缺少相同初始四格的真实ZC候选")
    markers = []
    regex = re.compile(r"首次采样跨阈值，Zone=(\d+)，主机代际=(\d+)，窗口毫秒=(\d+)")
    for i, line in enumerate(lines, 1):
        if (m := regex.search(line)):
            stamp = PREFIX.match(line)
            markers.append({"zone": int(m[1]), "generation": int(m[2]), "ms": int(m[3]), "line": i,
                            "time": datetime.strptime(stamp[1], "%Y/%m/%d-%H:%M:%S.%f")})
    if {(m["zone"], m["generation"], m["ms"]) for m in markers} != {
            (r["Zone"], r["主机代际后"], r["窗口毫秒"]) for r in crossings}:
        raise ValueError("原生首次跨阈值记录与CSV不一致")
    events = [describe_rr(e, lines, start_time, close_line, markers) for e in parsed["rr"]]
    inside = [e for e in events if e["开始在关闭请求之前"]]
    after = [e for e in events if not e["开始在关闭请求之前"]]
    if "剩余四组192MiB字节比对通过" not in (run / "near-verify.stdout.log").read_text():
        raise ValueError("剩余192MiB校验未通过")
    return {"来源": str(run), "速率条件": level, "每文件目标读每秒": rate, "业务读取秒数": read_seconds,
            "实际每文件业务读次数": counts,
            "初始四格": initial, "软件身份": identity.decode().splitlines(), "首次采样跨阈值": crossings,
            "原集中大组源Zone": sources[2][0],
            "原集中大组同代RR次数": sum(e["源Zone"] == sources[2][0] and e["主机Reset代际"] == 0 for e in inside),
            "观察窗口内RR次数（按开始时刻）": len(inside), "观察窗口内已完成RR搬运字节": sum(e["实际迁移字节"] for e in inside),
            "初始ZC搬运字节": 64 << 20, "窗口开始后ZC搬运字节": sum(s[1] for i, s in stats if i > start_line),
            "窗口内RR事件": inside, "关闭请求后开始的维护事件": after,
            "剩余192MiB校验": "通过", "原生日志SHA256": hashlib.sha256(native.encode()).hexdigest()}


def validate_equal_runs(runs):
    # 计划相同还不够，迟滞少读的运行不能称作等读量对照。
    if len(runs) != 2 or {r["速率条件"] for r in runs} != {"equal-slow", "equal-fast"}:
        raise ValueError("等读量对照须包含慢速和快速各一轮")
    if any(r["实际每文件业务读次数"] != dict.fromkeys(range(4), 768) for r in runs):
        raise ValueError("等读量对照实际次数未全部达到768")
    if len({tuple(r["软件身份"]) for r in runs}) != 1 or runs[0]["初始四格"] != runs[1]["初始四格"]:
        raise ValueError("等读量对照软件或初始条件不一致")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--equal-count", action="store_true", help="严格核验两组实际每文件768次对照")
    args = parser.parse_args()
    try:
        runs = [analyze(p) for p in args.runs]
        if len({tuple(r["软件身份"]) for r in runs}) != 1 or any(r["初始四格"] != runs[0]["初始四格"] for r in runs):
            raise ValueError("各组软件或初始条件不一致")
        equal = any(r["速率条件"].startswith("equal-") for r in runs)
        if equal != args.equal_count:
            raise ValueError("等读量运行须显式使用--equal-count，不能混用两类实验口径")
        if equal:
            validate_equal_runs(runs)
        scope = ("两组各一轮、实际每文件768次，分别24秒/3秒发送，观察40秒；不是性能实验或统计概率。"
                 if equal else "三档各一轮功能观察，改变未来读量，不是公平吞吐/时延实验或统计概率。")
        with args.output.open("x", encoding="utf-8") as f:
            json.dump({"口径限制": [scope,
                                  "先完成固定失效；按各组发送计划读取，无补读写锁，原RR先于GC不变。",
                                  "跨阈值是首次采样观察，Reset稳定不保证原子读压力；未采样到的时间保持未知。",
                                  "RR按开始是否在窗口内计数，已在执行的维护可能在关闭请求后完成。",
                                  "迁移改变目标布局，后续共享目标区RR可能搬其他文件；不能只统计原始源地址。",
                                  "阈值4096、模式文件非正式QLC/数据库实验，不计算可避免成本或最终公式。"],
                       "真实运行": runs}, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"未来读取核验失败：{error}\n")
    print(f"固定未来读取窗口核验通过：{args.output}")


if __name__ == "__main__":
    main()
