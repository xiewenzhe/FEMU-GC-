#!/usr/bin/env python3
"""核验人工单次选区对照；分别记窗口与终态账目，不冒充最终算法收益。"""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from analyze_controlled_future import validate_samples, get_marker
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import parse_lines, CANDIDATE, PREFIX

MIB = 1 << 20
SOURCES = {2: 7784628224, 3: 7516192768}


def rows(path):
    return list(csv.DictReader(path.read_text().splitlines()))


def zone_shape(zones):
    """当前FEMU对满区报告无效写指针；满区按容量占满，不能对标记做地址减法。"""
    if len(zones) != 32:
        raise ValueError("终态不是32个Zone")
    shape = Counter()
    for i, zone in enumerate(zones):
        if zone["slba"] != i*524288 or zone["cap"] != 524288:
            raise ValueError("终态Zone几何不符")
        if i < 3:
            continue
        state, capacity, wp = zone["state"], zone["cap"], zone["wp"]
        if state == "FULL":
            if wp != (1 << 64)-1:
                raise ValueError("满区写指针不符合当前FEMU报告标记")
            written = capacity
        else:
            written = wp-zone["slba"]
            if state not in ("EMPTY", "CLOSED", "IMP_OPEN", "EXP_OPEN") or not 0 <= written <= capacity:
                raise ValueError("非满区状态或写指针不符")
            if state == "EMPTY" and written != 0:
                raise ValueError("空区写指针非区起点")
        shape[(state, written, capacity)] += 1
    return sorted([list(key)+[count] for key, count in shape.items()])


def selection_view(lines):
    """只生成空间资格解析视图；实际汇总单独核验，原候选与原日志均不改。"""
    view, pending, override = list(lines), [], None
    pattern = re.compile(r"实际集合，指定Zone=(\d+)，原合格区=(\d+)，实际选中=([01])")
    for index, line in enumerate(lines):
        prefix = PREFIX.match(line)
        candidate = CANDIDATE.fullmatch(prefix[3]) if prefix else None
        if candidate:
            pending.append((index, int(candidate[1]), int(candidate[10])))
        if "ZC实验单次选区：实际集合" in line:
            match = pattern.search(line)
            if not match:
                raise ValueError("实验实际集合记录格式不符")
            zone, original, selected = map(int, match.groups())
            eligible = {z for _, z, flag in pending if flag}
            if len(eligible) != original or (selected and zone not in eligible):
                raise ValueError("原空间资格与实际选择不匹配")
            override = (original, selected)
        if "自动ZC实际集合：" in line or "简单双因素实际集合：" in line:
            match = re.search(r"原合格区=(\d+)，实际选中=(\d+)", line)
            if not match:
                raise ValueError("自动实际集合记录格式不符")
            original, selected = map(int, match.groups())
            eligible = {z for _, z, flag in pending if flag}
            zone = re.search(r"，Zone=(\d+)", line)
            if len(eligible) != original or selected > original or (zone and int(zone[1]) not in eligible):
                raise ValueError("自动选择不符合原空间资格")
            fallback = "状态=单区预算不可满足，退回原批量策略" in line and selected == original
            if selected == 1 and not zone and not fallback:
                raise ValueError("自动单区选择缺少实际地址")
            override = (original, selected)
        if "GC 选区汇总：" in line:
            if override is not None:
                match = re.search(r"选中Zone=(\d+)", line)
                if not match or int(match[1]) != override[1]:
                    raise ValueError("实验实际集合与原生汇总不一致")
                view[index] = re.sub(r"选中Zone=\d+", f"选中Zone={override[0]}", line)
                override = None
            pending.clear()
    return view


def validate_counts(data, window):
    actual, times = {}, {}
    for row in data:
        i, count, ms = (int(row[k]) for k in ("组号", "累计业务4KiB读次数", "窗口毫秒"))
        if i not in SOURCES or not 0 <= count <= 768 or count < actual.get(i, 0) or ms < times.get(i, 0):
            raise ValueError("业务计数或采样时序不符")
        actual[i], times[i] = count, ms
    if actual != {2: 768, 3: 768} or any(ms < window * 1000 for ms in times.values()):
        raise ValueError("实际读取未达到两文件各768次或窗口不足")
    return actual


def validate_pair(runs):
    if len(runs) != 2 or {r["条件"] for r in runs} != {"cold", "hot"}:
        raise ValueError("完整对照必须冷热各一轮")
    for key in ("初始状态", "软件身份", "发送窗口", "实际业务读次数", "终态IO区形态"):
        if runs[0][key] != runs[1][key]:
            raise ValueError(f"对照不一致：{key}")
    for stage in (0, 2):
        for key in ("空闲字节", "有效字节", "满区可回收字节", "空闲比例"):
            if runs[0]["空间账目"][stage][key] != runs[1]["空间账目"][stage][key]:
                raise ValueError(f"初始ZC后或补齐终态空间不一致：{key}")


def validate_expiry_plan(data):
    expected = [{"失效组号": "3", "失效字节": str(64*MIB),
                 "失效阶段": "未来窗口后且追加写入前", "每文件未来业务读次数": "768",
                 "追加写入字节": str(512*MIB)}]
    if data != expected:
        raise ValueError("冷文件预声明失效计划不符")
    return data


def validate_expiry_pair(runs):
    """失效后占用差异本身是结果；不强行追加额外负载来抹平空间。"""
    if len(runs) != 2 or {r["条件"] for r in runs} != {"cold", "hot"}:
        raise ValueError("完整失效对照必须冷热各一轮")
    for run in runs:
        if run.get("冷文件计划失效") is not True or run.get("剩余64MiB与冷文件不存在校验") != "通过":
            raise ValueError("缺少同计划失效或终态校验")
        validate_expiry_plan(run["失效计划"])
    for key in ("初始状态", "软件身份", "发送窗口", "实际业务读次数", "失效计划"):
        if runs[0][key] != runs[1][key]:
            raise ValueError(f"失效对照不一致：{key}")
    for key in ("空闲字节", "有效字节", "满区可回收字节", "空闲比例"):
        if runs[0]["空间账目"][0][key] != runs[1]["空间账目"][0][key]:
            raise ValueError(f"首次回收后空间不一致：{key}")
    if runs[0]["空间账目"][-1]["有效字节"] != runs[1]["空间账目"][-1]["有效字节"]:
        raise ValueError("同输入终态有效数据量不一致")
    cold, hot = (next(r for r in runs if r["条件"] == condition) for condition in ("cold", "hot"))
    a, b = cold["空间账目"][-1], hot["空间账目"][-1]
    return {"热区优先减去冷区优先的终态空闲字节": b["空闲字节"]-a["空闲字节"],
            "热区优先减去冷区优先的完整搬运字节": hot["全部维护搬运字节"]-cold["全部维护搬运字节"],
            "热区优先终态空间是否不劣": b["空闲字节"] >= a["空闲字节"] and b["满区可回收字节"] <= a["满区可回收字节"]}


def validate_weighted_input(run, lines, level, chosen, future_start):
    """核验真实打分和自动决策，不从预期RR结果反推选择。"""
    mode = "valid" if level == "cold" else "weighted"
    config = rows(run / "weighted-config.csv")
    weights = (100, 0) if mode == "valid" else (50, 50)
    if config != [{"模式": mode, "回收预算字节": str(192*MIB),
                   "有效量权重百分比": str(weights[0]), "块读数权重百分比": str(weights[1]),
                   "是否传入人工受害地址": "0"}]:
        raise ValueError("双因素配置不符")
    text = "\n".join(lines)
    if "自动ZC：" in text or "ZC实验单次选区：" in text or (run/"training-counts.csv").exists() or (run/"expiry-plan.csv").exists():
        raise ValueError("双因素混入趋势、人工源地址或未来失效计划")
    pattern = re.compile(r"简单双因素实际集合：模式=(valid|weighted)，原合格区=(\d+)，实际选中=1，Zone=(\d+)，回收预算=(\d+)，实际使用等权=(\d+)，等权分数ppm=(\d+)")
    decisions = [(i, match.groups()) for i, line in enumerate(lines, 1) if (match := pattern.search(line))]
    if not decisions:
        raise ValueError("没有真实双因素自动决策")
    first_line, first = decisions[0]
    if first[:5] != (mode, "2", str(chosen), str(192*MIB), str(int(mode == "weighted"))) or not first_line < future_start:
        raise ValueError("双因素首区决策不符")
    candidate = re.compile(r"简单双因素候选：Zone=(\d+)，有效字节=(\d+)，容量字节=(\d+)，最大块读数=(\d+)，读数阈值=(\d+)，覆盖资格=(\d+)，空目标资格=(\d+)，读压力可用=(\d+)，空间分量ppm=(\d+)，风险分量ppm=(\d+)，等权分数ppm=(\d+)")
    latest = {}
    for line in lines[:first_line-1]:
        if match := candidate.search(line):
            value = tuple(map(int, match.groups())); latest[value[0]] = value
    if set(latest) != set(SOURCES.values()):
        raise ValueError("双因素首区候选不完整")
    for group, zone in SOURCES.items():
        value = latest[zone]
        maximum = 3584 if group == 2 else 448
        space, risk = 750000, maximum*1000000//4096
        if value[1:] != (64*MIB, 256*MIB, maximum, 4096, 1, 1, 1, space, risk, (space+risk)//2):
            raise ValueError("双因素原始输入或50/50归一化打分不符")
    selected = min(latest.values(), key=lambda v: (v[1], v[0]) if mode == "valid" else (-v[10], v[1], v[0]))
    if selected[0] != chosen or int(first[5]) != selected[10]:
        raise ValueError("实际选择不符合单因子或等权排序")
    return {"简单双因素模式": mode, "双因素配置": config,
            "首次双因素输入": list(latest.values()), "全部双因素决策": decisions,
            "人工受害地址注入": False}


def analyze(run):
    levels = [p for p in ("smoke", "cold", "hot", "expire-smoke", "expire-cold", "expire-hot",
                         "auto-smoke", "auto-space", "auto-rr", "weighted-smoke", "weighted-valid", "weighted-rr")
              if (run / f"choice-{p}.exit").exists()]
    if len(levels) != 1:
        raise ValueError("条件不明确")
    mode = levels[0]
    automatic = mode.startswith("auto-")
    weighted = mode.startswith("weighted-")
    expiry = automatic or mode.startswith("expire-")
    level = {"auto-smoke": "smoke", "auto-space": "cold", "auto-rr": "hot",
             "weighted-smoke": "smoke", "weighted-valid": "cold", "weighted-rr": "hot"}.get(mode, mode.removeprefix("expire-"))
    verify_phase = "choice-weighted-verify" if weighted else "choice-auto-verify" if automatic else "choice-expire-verify" if expiry else "choice-verify"
    for phase in ("mkfs", "runner", f"choice-{mode}") + (() if level == "smoke" else (verify_phase,)):
        if (run / f"{phase}.exit").read_text().strip() != "0":
            raise ValueError(f"未完成：{phase}")
    identity = (run / "identity.initial.sha256").read_text()
    if identity != (run / "identity.final.sha256").read_text() or len(identity.splitlines()) != (8 if automatic or weighted else 7):
        raise ValueError("测试源码/库/程序身份变化或不完整")
    for line in (run / "logs.sha256").read_text().splitlines():
        digest, guest = line.split()
        if hashlib.sha256((run / Path(guest).name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"日志哈希不符：{guest}")
    initial = [{k: int(v) for k, v in row.items()} for row in rows(run / "choice-initial.csv")]
    expected = [{"组号": i, "Zone字节地址": SOURCES[i], "有效字节": 64*MIB, "无效字节": 192*MIB,
                 "最大读数": 3584 if i == 2 else 448, "读数总和": 3584, "达阈值块": 0, "空闲比例": 10}
                for i in (2, 3)]
    if initial != expected:
        raise ValueError("初始同等空间/不同读风险条件不符")
    rate, seconds, window = (256, 3, 20) if level == "smoke" else (32, 24, 40)
    config = [{k: int(v) for k, v in row.items()} for row in rows(run / "window.csv")]
    if config != [{"每文件目标读每秒": rate, "业务读取秒数": seconds, "观察窗口秒数": window, "采样间隔毫秒": 100}]:
        raise ValueError("发送窗口不符")
    counts = validate_counts(rows(run / "read-counts.csv"), window)
    validate_samples([{k: int(v) for k, v in row.items()} for row in rows(run / "pressure.csv")])
    states = [{k: v if k == "阶段" else int(v) for k, v in row.items()} for row in rows(run / "choice-states.csv")]
    stages = ["初始ZC后", "未来窗口后"] + (["冷文件失效后"] if expiry else []) + ["相同追加写入并补齐后"]
    if [s["阶段"] for s in states] != stages:
        raise ValueError("空间账目阶段不完整")
    if states[-1]["热源代际"] != 1 or states[-1]["冷源代际"] != 1:
        raise ValueError("终态两源未各成功Reset一次")
    expected_generations = (0, 1) if level == "cold" else (1, 0)
    if (states[0]["热源代际"], states[0]["冷源代际"]) != expected_generations:
        raise ValueError("初始ZC实际重置源不符")
    zones = json.loads((run / "zones.final.json").read_text())["zone_list"]
    shape = zone_shape(zones)
    text = (run / f"zenfs.choice-{mode}.log").read_text()
    lines = text.splitlines()
    parsed = parse_lines(selection_view(lines), 64)
    if "[ERROR]" in text or parsed["failed_resets"] or any(e["status"] != "完成" for e in parsed["rr"]):
        raise ValueError("维护错误或RR未完成")
    start, _ = get_marker(lines, "开始，每文件目标")
    close, _ = get_marker(lines, "窗口关闭请求")
    starts = [i for i, line in enumerate(lines, 1) if "受控等预算选区：空间补齐开始" in line]
    ends = [i for i, line in enumerate(lines, 1) if "受控等预算选区：空间补齐结束" in line]
    if len(starts) != 1 or len(ends) != 1 or not start < close < starts[0] < ends[0]:
        raise ValueError("读取与空间补齐边界不符")
    if expiry:
        plan = validate_expiry_plan(rows(run / "expiry-plan.csv"))
        begin = [i for i, line in enumerate(lines, 1) if "冷文件计划失效开始，组号=3，失效字节=67108864" in line]
        done = [i for i, line in enumerate(lines, 1) if "冷文件计划失效完成，组号=3，失效字节=67108864" in line]
        deleted = [i for i, line in enumerate(lines, 1) if "DeleteFile: /controlled-zc/case3.live.sst" in line]
        if len(begin) != 1 or len(done) != 1 or len(deleted) != 1 or not close < begin[0] < deleted[0] < done[0] < starts[0]:
            raise ValueError("冷文件实际失效次数或边界不符")
        if any(s["冷文件Zone"] != 0 for s in states[2:]) or states[2]["有效字节"] != states[1]["有效字节"]-64*MIB:
            raise ValueError("失效后冷文件仍有地址或有效字节未减少64MiB")
        if states[-1]["有效字节"] != states[2]["有效字节"]+512*MIB:
            raise ValueError("追加写入未增加512MiB有效数据")
    chosen = SOURCES[3 if level == "cold" else 2]
    if weighted:
        weighted_details = validate_weighted_input(run, lines, level, chosen, start)
    elif automatic:
        model_mode = "space" if level == "cold" else "rr"
        auto_config = rows(run / "auto-config.csv")
        if auto_config != [{"模式": model_mode, "回收预算字节": str(192*MIB), "估计窗口秒": "40", "是否传入人工受害地址": "0"}]:
            raise ValueError("自动模式预算或禁止人工地址配置不符")
        training = rows(run / "training-counts.csv")
        if training != [{"组号": str(i), "实际业务4KiB读次数": "768", "计划读每秒": "32", "发送秒数": "24"} for i in (2, 3)]:
            raise ValueError("决策前两文件真实读取不符")
        actual = re.findall(r"自动ZC实际集合：模式=(space|rr)，原合格区=(\d+)，实际选中=1，Zone=(\d+)，回收预算=(\d+)，净回收参考=(\d+)，增量字节估计=(-?\d+)", text)
        expected_cost = 64*MIB if model_mode == "space" else 0
        if actual != [(model_mode, "2", str(chosen), str(192*MIB), str(192*MIB), str(expected_cost))] or "ZC实验单次选区：" in text:
            raise ValueError("自动选区实际集合或无人工选择边界不符")
        decision_line = next(i for i, line in enumerate(lines, 1) if "自动ZC实际集合：" in line and "实际选中=1，Zone=" in line)
        model_pattern = re.compile(r"自动ZC候选：Zone=(\d+)，当前复制=(\d+)，净回收参考=(\d+)，覆盖资格=(\d+)，空目标资格=(\d+)，估计可用=(\d+)，窗口增长估计=(\d+)，不选RR字节估计=(\d+)，选后RR字节保护项=(\d+)")
        latest = {}
        for line in lines[:decision_line-1]:
            match = model_pattern.search(line)
            if match:
                values = tuple(map(int, match.groups()))
                latest[values[0]] = values
        if set(latest) != set(SOURCES.values()):
            raise ValueError("自动决策没有完整冷热候选成本输入")
        for group, zone in SOURCES.items():
            v = latest[zone]
            if v[1:5] != (64*MIB, 192*MIB, 1, 1):
                raise ValueError("自动候选复制、回收或安全资格不符")
            if model_mode == "space":
                if v[5:] != (0, 0, 0, 0):
                    raise ValueError("空间参考组混入预计RR抵扣")
            else:
                maximum = 3584 if group == 2 else 448
                expected_rr = 64*MIB if maximum+v[6] >= 4096 else 0
                if v[5] != 1 or v[6] >= 4096 or v[7:] != (expected_rr, 0):
                    raise ValueError("摘要增长、冻结有效量RR估计或目标保护项不符")
        if not decision_line < start:
            raise ValueError("自动决策未发生在未来读取之前")
    else:
        actual = re.findall(r"ZC实验单次选区：实际集合，指定Zone=(\d+)，原合格区=(\d+)，实际选中=(\d+)", text)
        if [item for item in actual if item[2] == "1"] != [(str(chosen), "2", "1")]:
            raise ValueError("缺少唯一实际单区选择")
    if set(re.findall(r"GC 检查：.*启动水平=(\d+)%", text)) != {"20"}:
        raise ValueError("缺少唯一实际单区选择或GC20%")
    stats = [(i, tuple(map(int, match.groups()))) for i, line in enumerate(lines, 1) if (match := ZC_STATS.search(line))]
    before = [s for i, s in stats if i < start]
    if len(before) != 1 or before[0][1:] != (64*MIB, 1) or any(e["line"] < start for e in parsed["rr"]):
        raise ValueError("初始ZC不是同预算64MiB/一源Reset，或未来读取前已有RR")
    names_before = re.findall(r"MigrateFileExtents, fname: /controlled-zc/case([23])\.live\.sst,", "\n".join(lines[:start-1]))
    if names_before != ["3" if level == "cold" else "2"]:
        raise ValueError("初始ZC真实迁移文件与实际选择不符")
    for e in parsed["rr"]:
        names = re.findall(r"MigrateFileExtents, fname: /controlled-zc/case([23])\.live\.sst,", "\n".join(lines[e["line"]-1:e["end_line"]]))
        if not names or len(names) != len(set(names)) or e["moved"] != len(names)*64*MIB or e["resets"] != 1:
            raise ValueError("RR实际文件和搬运统计不符")
    inside = [e for e in parsed["rr"] if start < e["line"] < close]
    window_zc = sum(s[1] for i, s in stats if start < i < starts[0])
    total_zc = sum(s[1] for _, s in stats)
    total_rr = sum(e["moved"] for e in parsed["rr"])
    verify_text = ((run / f"choice-{mode}.stdout.log").read_text() if level == "smoke"
                   else (run / f"{verify_phase}.stdout.log").read_text())
    proof = "64MiB字节比对通过，冷文件不存在" if expiry else "128MiB字节比对通过"
    if proof not in verify_text:
        raise ValueError("剩余数据字节比对或冷文件不存在校验未通过")
    if level != "smoke":
        verify_log = (run / f"zenfs.{verify_phase}.log").read_text()
        verify_parsed = parse_lines(verify_log.splitlines(), 64)
        if "[ERROR]" in verify_log or verify_parsed["rr"] or verify_parsed["failed_resets"] or ZC_STATS.search(verify_log):
            raise ValueError("校验阶段有额外维护或错误")
    result = {"来源": str(run), "条件": level, "初始状态": initial, "软件身份": identity.splitlines(),
            "发送窗口": config[0], "实际业务读次数": counts, "空间账目": states, "终态IO区形态": shape,
            "初始选中Zone": chosen, "初始ZC搬运字节": 64*MIB,
            "窗口RR次数": len(inside), "窗口RR搬运字节": sum(e["moved"] for e in inside),
            "窗口额外ZC搬运字节": window_zc, "补齐阶段ZC搬运字节": sum(s[1] for i, s in stats if i > starts[0]),
            "全部ZC搬运字节": total_zc, "全部RR搬运字节": total_rr, "全部维护搬运字节": total_zc+total_rr,
            "全部RR事件": parsed["rr"], "全部ZC轮次": [s for _, s in stats]}
    if expiry:
        expiry_resets = [e for e in parsed["barriers"] if begin[0] < e["line"] < done[0]
                         and e["reason"].startswith("主机成功 Reset：")]
        expected_resets = [] if level == "cold" else [SOURCES[3]]
        if [e["zone"] for e in expiry_resets] != expected_resets:
            raise ValueError("冷文件失效阶段的真实Reset与源代际变化不一致")
        occupied_invalid = 29*256*MIB-states[-1]["空闲字节"]-states[-1]["有效字节"]
        if occupied_invalid < states[-1]["满区可回收字节"]:
            raise ValueError("终态占用与满区回收口径矛盾")
        result.update({"冷文件计划失效": True, "失效计划": plan, "剩余64MiB与冷文件不存在校验": "通过",
                       "失效阶段正常Reset事件": expiry_resets,
                       "终态IO容量减空闲减有效字节": occupied_invalid,
                       "终态未计入满区可回收口径的占用字节": occupied_invalid-states[-1]["满区可回收字节"],
                       "失效阶段RR搬运字节": sum(e["moved"] for e in parsed["rr"] if begin[0] < e["line"] < done[0]),
                       "失效阶段ZC搬运字节": sum(s[1] for i, s in stats if begin[0] < i < done[0])})
    else:
        result["剩余128MiB校验"] = "通过"
    if automatic:
        result.update({"自动机制模式": model_mode, "自动机制配置": auto_config,
                       "决策前真实读取": training, "自动首区成本输入": list(latest.values()),
                       "人工受害地址注入": False})
    if weighted:
        result.update(weighted_details)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--cold-expiry", action="store_true")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--weighted", action="store_true")
    args = parser.parse_args()
    try:
        runs = [analyze(p) for p in args.runs]
        if any(r.get("冷文件计划失效", False) != args.cold_expiry for r in runs):
            raise ValueError("失效实验必须显式使用--cold-expiry，不得混用历史无失效对照")
        if any(("自动机制模式" in r) != args.auto for r in runs):
            raise ValueError("自动机制必须显式使用--auto，不得与人工选区混用")
        if any(("简单双因素模式" in r) != args.weighted for r in runs):
            raise ValueError("双因素实验必须显式使用--weighted，不得混用历史或趋势模式")
        comparison = {}
        if args.smoke:
            if len(runs) != 1 or runs[0]["条件"] != "smoke":
                raise ValueError("短测身份不符")
        else:
            if args.cold_expiry:
                comparison = validate_expiry_pair(runs)
            else:
                validate_pair(runs)
        limitations = ["显式指定首次单区选择，非原批量策略对照，非最终RR-aware模型。",
                       "原候选字段是空间资格；原生汇总与实验实际集合独立核验，再生成资格汇总解析视图，原日志不修改。",
                       "两区空间相同、读风险不同；默认关闭实验开关，首次选择后恢复原策略。",
                       "窗口空间可能不同，不能只用窗口RR下降宣称净收益；终态须比较空间和完整搬运账目。",
                       "相同追加512MiB真实写入，不直接调用维护；模式文件非真实SST，阈值4096未校准。"]
        if args.cold_expiry:
            limitations += ["两组执行同一预声明失效计划；它不是在线预测器，也不是已实现的新评分策略。",
                            "失效阶段正常零搬运Reset单列，不计作ZC/RR搬运；未测设备全部擦除和性能。",
                            "终态空间差异保留；满区可回收为零不代表部分区没有无效占用。",
                            "只核验有限窗口及相同追加负载，不能外推长期维护或真实数据库收益。"]
        if args.auto:
            limitations[0] = "默认关闭的首版自动单区成本比较，对照为同回收预算的空间单区版本，非原批量策略或最终模型。"
            limitations[2] = "候选空间准入及GC20%不变；自动比较使用已发生的摘要趋势，不注入人工受害地址。"
            limitations += ["最大值斜率不等于固定物理块速率；冻结当前有效量估计一次RR，无未来失效预测器。",
                            "空目标及目标重累积保护只是验证性近似，非完整迁移目标规划或长期成本估计。"]
        if args.weighted:
            limitations[0] = "用户确认两组每轮单区：baseline只按有效数据量，新机制有效量与最大块读数各50%；非stock批量ZenFS。"
            limitations[2] = "原空间准入及GC20%不变，明确归一化、同回收预算，不用趋势、未来预测或人工victim地址。"
            limitations += ["受控初始两候选有效量相同，baseline按地址平局；尚不覆盖不同有效量的大规模设备实验。",
                            "RR数量下降不等于总维护搬运下降；必须同时报告ZC与完整成本，未做性能结论。"]
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump({"口径限制": limitations,
                       "真实运行": runs, "失效配对比较": comparison}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"等预算选区核验失败：{error}\n")
    print(f"等预算选区核验通过：{args.output}")


if __name__ == "__main__":
    main()
