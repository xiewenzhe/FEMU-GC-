#!/usr/bin/env python3
"""独立重算成本观察；只解释当时可见信息，不将有效量下降等同于未来退役。"""
import json
from pathlib import Path
import sys
from collections import Counter, deque
from analyze_short_shadow_stream import analyze as base_analyze, rows
from analyze_v1_shadow_stream import fields, require, SECOND
from analyze_v1_growth_stream import qualifies


def epoch(p):
    return tuple(p[k] for k in ("会话", "Zone", "设备代际", "主机代际", "主机序号"))


def window_change(history, current, known, seconds):
    # 输出顺序对应真实日志；间隔是约10/30秒的端点差，不冒充精确滚动窗口。
    empty = (False, False, 0, 0, 0, 0, 0)
    now = int(current["时点纳秒"])
    if not known or seconds not in (10, 30) or now < seconds*SECOND:
        return empty
    target = now-seconds*SECOND
    for frame in reversed(history):
        p = frame.get(current["Zone"])
        if not p or p["可知"] != "1" or epoch(p) != epoch(current) or int(p["时点纳秒"]) >= now:
            return empty
        previous = int(p["时点纳秒"])
        if previous > target:
            continue
        before, after = int(p["有效字节"]), int(current["有效字节"])
        interval = now-previous
        if target-previous > 2*SECOND:
            return False, True, previous, before, interval, 0, 0
        return True, True, previous, before, interval, max(0, before-after), max(0, after-before)
    return empty


def physical_bounds(length, alignment, header=8):
    if length <= 0 or alignment <= 0:
        return False, 0, 0
    low = (length+alignment-1)//alignment*alignment
    high = (length+header+alignment-1)//alignment*alignment
    return (True, low, high) if high < 2**32 else (False, 0, 0)


def fragment_summary(parts, alignment):
    lower = upper = maximum = covered = 0
    known = True
    for index, p in enumerate(parts, 1):
        require(int(p["次序"]) == index, "片段次序不连续")
        expected = physical_bounds(int(p["逻辑字节"]), alignment)
        require((bool(int(p["物理界限可知"])), int(p["物理下界字节"]), int(p["物理上界字节"])) == expected,
                "片段物理上下界不符")
        known = known and expected[0]
        covered += int(p["逻辑字节"])
        lower += expected[1]; upper += expected[2]; maximum = max(maximum, expected[2])
    first = physical_bounds(int(parts[0]["逻辑字节"]), alignment)[1:] if parts else (0, 0)
    return len(parts), covered, known, lower, upper, maximum, first[0], first[1]


def tail_flags(target, physical):
    count, _, known, _, upper, _, first_lower, _ = physical
    usable = target["可知"] == "1" and target["存在"] == "1" and known and count > 0
    remaining = int(target["剩余字节"])
    return bool(usable and first_lower > remaining), bool(usable and upper <= remaining)


def inspect(lines, decisions, alignment):
    expected_zones = {str(i*(256<<20)) for i in range(3, 32)}
    history, frame, frame_key, current = deque(maxlen=40), {}, None, {}
    simple, recent, parts, changes, targets, observed = {}, {}, {}, {}, {}, []
    samples = []

    def flush():
        nonlocal frame, frame_key, current
        if not frame:
            return
        require(set(frame) == expected_zones, "成本采样不是完整29个IO区")
        stage = next(iter(frame.values()))["阶段"]
        if stage == "等待":
            first = next(iter(frame.values()))
            if history and next(iter(history[-1].values()))["会话"] != first["会话"]:
                history.clear()
            require(not history or int(next(iter(history[-1].values()))["时点纳秒"]) < int(first["时点纳秒"]),
                    "有效量等待采样时间不递增")
            history.append(frame)
        else:
            require(stage == "选区", "成本采样阶段未知")
            current = frame
        frame, frame_key = {}, None

    for number, line in enumerate(lines, 1):
        if "成本有效量采样：" in line:
            p = fields(line, "成本有效量采样：")
            key = (p["阶段"], p["会话"], p["时点纳秒"])
            if frame_key is not None and key != frame_key:
                flush()
            frame_key = key
            require(p["Zone"] not in frame and p["可知"] in ("0", "1") and
                    int(p["时点纳秒"]) > 0 and int(p["有效字节"]) >= 0, "成本采样字段不合法")
            frame[p["Zone"]] = dict(p, 行号=number)
            samples.append(dict(p, 行号=number))
            continue
        # 其他线程可在同一批29行之间写日志；仅在采样线程自己的结束标记处闭合。
        if "V1增长采样：" in line or "成本预留观察：" in line:
            flush()
        if "V1增长采样：" in line and fields(line, "V1增长采样：")["可用"] != "1":
            history.clear()
        if "简单双因素候选：" in line:
            c = fields(line, "简单双因素候选："); simple[c["Zone"]] = c
        if "V1影子候选：" in line:
            b = fields(line, "V1影子候选："); recent[b["Zone"]] = b
        if "成本预留观察：" in line:
            t = fields(line, "成本预留观察："); rid = t["轮次"]
            require(rid in decisions and rid not in targets and current, "预留轮次重复或当前采样缺失")
            require(all(t[k] in ("0", "1") for k in ("启用", "可知", "存在")) and t["启用"] == "1", "预留开关或标志不符")
            targets[rid] = t
            if decisions[rid]["帧可用"] != "1":
                history.clear()
        if "成本有效量变化：" in line:
            c = fields(line, "成本有效量变化："); rid, zone = c["轮次"], c["Zone"]
            require(zone in current and rid in targets, "变化缺当前区或预留记录")
            p = current[zone]; known = c["当前可知"] == "1"
            require(c["当前可知"] in ("0", "1") and (not known or p["可知"] == "1" and
                    recent[zone].get("统计准入") == "1" and p["有效字节"] == simple[zone]["有效字节"] and
                    p["设备代际"] == recent[zone]["设备代际"]), "当前有效量准入矛盾")
            require(c["当前时点纳秒"] == p["时点纳秒"] and c["当前有效字节"] == p["有效字节"], "当前有效量点不匹配")
            expected = window_change(history, p, known, int(c["窗口秒"]))
            values = (bool(int(c["历史可知"])), bool(int(c["有前点"])),
                      *(int(c[k]) for k in ("前时点纳秒", "前有效字节", "间隔纳秒", "减少字节", "增加字节")))
            require(values == expected, "历史端点、Reset或有效量差重算不符")
            key = (rid, zone, c["窗口秒"])
            require(key not in changes, "有效量窗口重复"); changes[key] = c
        if "成本片段观察：" in line:
            p = fields(line, "成本片段观察：")
            parts.setdefault((p["轮次"], p["Zone"]), []).append(p)
        if "成本候选观察：" in line:
            c = fields(line, "成本候选观察："); rid, zone = c["轮次"], c["Zone"]
            d = decisions[rid]; raw = simple[zone]
            greedy = simple.get(d["实际Greedy"]) if d["存在Greedy"] == "1" else None
            reclaim = int(raw["容量字节"])-int(raw["有效字节"])
            extra = max(0, int(raw["有效字节"])-int(greedy["有效字节"])) if greedy else 0
            loss = max(0, int(greedy["容量字节"])-int(greedy["有效字节"])-reclaim) if greedy else 0
            require(c["有效字节"] == raw["有效字节"] and
                    tuple(int(c[k]) for k in ("回收字节", "额外有效字节", "回收损失字节")) == (reclaim, extra, loss), "空间成本差重算不符")
            require(int(c["原资格"]) == qualifies(raw) and c["实际改选"] == "0" and
                    int(c["是Greedy"]) == bool(greedy and zone == d["实际Greedy"]) and
                    int(c["是影子建议"]) == bool(d["会改选"] == "1" and zone == d["建议Zone"]), "成本观察改变角色或候选资格")
            physical = fragment_summary(parts.get((rid, zone), []), alignment)
            require(tuple(int(c[k]) for k in ("片段数", "覆盖字节", "物理界限可知", "物理总下界字节",
                    "物理总上界字节", "最大物理上界字节", "首片段下界字节", "首片段上界字节")) == physical, "片段覆盖或总物理边界不符")
            require(tuple(bool(int(c[k])) for k in ("预留首片段确定不适配", "预留全部片段确定可容纳")) ==
                    tail_flags(targets[rid], physical), "预留适配标志重算不符")
            require((rid, zone, "10") in changes and (rid, zone, "30") in changes, "有效量窗口缺失")
            observed.append(dict(c, 行号=number, 约十秒有效量变化=changes[rid, zone, "10"],
                约三十秒有效量变化=changes[rid, zone, "30"], 当前预留=targets[rid], 片段=parts.get((rid, zone), [])))
        if "简单双因素实际集合：" in line:
            simple, recent, current = {}, {}, {}
    flush()
    require(not simple and not frame and set(targets) == set(decisions), "成本日志轮次未闭合")
    require(len(observed)*2 == len(changes) and sum(int(c["片段数"]) for c in observed) == sum(map(len, parts.values())), "成本候选或片段有遗漏")
    return observed, samples


def analyze(run):
    base = base_analyze(run)
    lines = (run/"zenfs.run.log").read_text().splitlines()
    require(sum("成本观察：已启用" in line for line in lines) == 1, "成本观察未唯一启用")
    probe_files = list(run.parent.glob("cost_device_probe*.csv"))
    require(len(probe_files) == 1, "实际只读接口对齐记录缺失或不唯一")
    probe = rows(probe_files[0]); require(len(probe) == 1, "只读探测记录非法")
    alignment = int(probe[0]["对齐字节"])
    require(alignment > 0 and {k:v for k,v in probe[0].items() if k != "对齐字节"} ==
            {"IO区数":"29", "读取数据页":"0", "写入或重置":"0", "预留启用":"0", "预留可知":"0"}, "只读接口核验不符")
    candidates, samples = inspect(lines, {d["轮次"]:d for d in base["决策"]}, alignment)
    lookup = {(o["轮次"], str(o["Zone"])):o for o in base["非Greedy候选后续观测"]}
    for c in candidates:
        outcome = lookup.get((c["轮次"], c["Zone"]))
        if outcome:
            c["后续观测"] = outcome
    suggested = [c for c in candidates if c["是影子建议"] == "1"]
    return dict(核验通过=True, 来源=str(run), 实际策略="Greedy", 实际改选次数=0,
        实际对齐字节=alignment, 有效量采样条数=len(samples), 成本候选数=len(candidates),
        历史窗口已知数=sum(int(c[k]["历史可知"]) for c in candidates for k in ("约十秒有效量变化", "约三十秒有效量变化")),
        候选成本=candidates, 建议候选成本=suggested, 短期影子及完整业务核验=base,
        建议候选尾部标志=dict(Counter(c["预留首片段确定不适配"]+"/"+c["预留全部片段确定可容纳"] for c in suggested)),
        当前成本已用于实际选择=False, 已验证净收益=False,
        说明="有效量差是同区同代际净端点变化，不区分删除和迁移，不能推断剩余寿命。物理量给未知稀疏头的上下界；预留适配不代表实际目标选择，也不保证后续不会重复搬运。")


if __name__ == "__main__":
    require(len(sys.argv) == 2, "需要本轮原始结果目录")
    print(json.dumps(analyze(Path(sys.argv[1])), ensure_ascii=False, indent=2))
