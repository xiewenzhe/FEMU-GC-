#!/usr/bin/env python3
"""独立核验短期影子建议及后续同块事件；实际仍Greedy，不推断改选净收益。"""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
from collections import Counter, defaultdict
from statistics import mean
from analyze_stream import check_trace
from analyze_periodic import latency, validate_decisions
from analyze_controlled_choice import selection_view
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import parse_lines, RR_STATS, RR_START
from analyze_v1_shadow_stream import fields, predict, eta, require, SECOND, MAX, BUDGET, EXTRA
from analyze_v1_growth_pair import check_gate
from analyze_v1_growth_stream import qualifies
from analyze_v1_trajectory import read_samples, point_from_candidate, cross, identity
from analyze_v1_victim_value import stamp


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def short_eta(block, growth):
    # 先由旧独立检查器校验同块身份、Reset、真实增量和原增长门槛。
    check_gate(block, growth)
    if not block or block.get("统计准入") != "1" or growth["增量可知"] != "1":
        return None
    if int(block["当前RC"]) >= 4096:
        return 0
    delta, span = int(growth["真实增量"]), int(growth["间隔纳秒"])
    return min(MAX, ((4096-int(block["当前RC"]))*span+delta-1)//delta) if delta else MAX


def choose_short(candidates, estimates):
    eligible = [c for c in candidates if qualifies(c)]
    if not eligible:
        return None, None, False, 0, MAX, "没有合格候选"
    greedy = min(eligible, key=lambda c: (int(c["有效字节"]), int(c["Zone"])))
    zone = int(greedy["Zone"])
    ge = estimates.get(zone)
    if ge is None:
        return zone, zone, False, 0, MAX, "Greedy同块近期统计未知"
    if ge <= 10*SECOND:
        return zone, zone, False, 0, ge, "Greedy同块近期已经紧迫"
    best, reason = None, "没有同块近期紧迫候选"
    for c in eligible:
        other = int(c["Zone"])
        predicted = estimates.get(other)
        if other == zone or predicted is None or predicted > 10*SECOND:
            continue
        extra = max(0, int(c["有效字节"])-int(greedy["有效字节"]))
        if extra > EXTRA:
            if best is None:
                reason = "短期紧迫候选超出额外有效搬运预算"
            continue
        key = (predicted, int(c["有效字节"]), other, extra)
        if best is None or key[:3] < best[:3]:
            best = key
    if best is None:
        return zone, zone, False, 0, MAX, reason
    return zone, best[2], True, best[3], best[0], "同块近期紧迫且额外有效搬运在预算内"


def rounds(lines):
    pending, blocks, gates, shorts = [], {}, {}, {}
    proposal = decision = old = None
    decisions, observations, files = [], [], []
    for number, line in enumerate(lines, 1):
        if "简单双因素候选：" in line:
            pending.append(fields(line, "简单双因素候选："))
        if "V1影子候选：" in line:
            b = fields(line, "V1影子候选：")
            z = int(b["Zone"])
            require(z not in blocks, "近期候选重复")
            blocks[z] = b
            if "当前RC" in b:
                rc, d10, d30 = (int(b[k]) for k in ("当前RC", "十秒真实增量", "三十秒真实增量"))
                now, born, flags = (int(b[k]) for k in ("快照纳秒", "代际起点纳秒", "窗口标志"))
                require(born <= now and 0 <= d10 <= d30 <= rc and flags & 1 and not flags & ~15,
                        "候选窗口或读数矛盾")
                require(not flags & 2 or now-born >= 10*SECOND, "十秒窗口未预热")
                require(not flags & 4 or (now-born >= 30*SECOND and flags & 2 and not flags & 8), "三十秒窗口矛盾")
                point = point_from_candidate(b)
                require(point["block"] == z//(256<<20) and 0 <= point["ch"] < 8 and
                        0 <= point["lun"] < 4 and 0 <= point["plane"] < 2 and
                        point["block_id"] == (point["ch"]*4+point["lun"])*2+point["plane"], "候选物理块身份非法")
                require(int(b["预测可知"]) == bool(b["统计准入"] == "1" and eta(b) is not None), "原预测标志矛盾")
                require(int(b["预测达阈值纳秒"]) == (MAX if eta(b) is None else eta(b)), "原预测未正确记录")
        if "V1影子文件：" in line:
            files.append(dict(fields(line, "V1影子文件："), 行号=number))
        if "V1增长候选：" in line:
            g = fields(line, "V1增长候选："); z = int(g["Zone"])
            require(z not in gates, "增长候选重复"); gates[z] = g
        if "V1增长决策：" in line:
            require(proposal is None, "增长决策重复"); proposal = fields(line, "V1增长决策：")
        if "短期影子候选：" in line:
            s = fields(line, "短期影子候选："); z = int(s["Zone"])
            require(z not in shorts, "短期候选重复"); shorts[z] = s
        if "短期影子决策：" in line:
            require(decision is None, "短期决策重复")
            decision = dict(fields(line, "短期影子决策："), 行号=number)
        if "V1影子决策：" in line:
            require(old is None, "原影子决策重复"); old = fields(line, "V1影子决策：")
        if "简单双因素实际集合：" not in line:
            continue
        require(decision is not None and old is not None and proposal is not None, "完整影子决策缺失")
        round_id = str(len(decisions)+1)
        require(all(d["轮次"] == round_id for d in (decision, old, proposal)), "影子轮次不连续")
        require(all(d[k] in ("0", "1") for d in (decision, old)
                    for k in ("存在Greedy", "会改选", "实际改选", "帧可用")), "决策布尔标志非法")
        zones = {int(c["Zone"]) for c in pending}
        require(len(zones) == len(pending) and zones == set(blocks) == set(gates) == set(shorts), "候选记录不完整或重复")
        usable = {z:b for z,b in blocks.items() if b.get("统计准入") == "1"}
        estimates = {}
        for c in pending:
            z = int(c["Zone"]); b, g, s = blocks[z], gates[z], shorts[z]
            require(g["轮次"] == s["轮次"] == b["轮次"] == round_id, "候选轮次不一致")
            require(c["有效字节"] == b["有效字节"] == s["有效字节"], "候选有效量不一致")
            if "当前RC" in b:
                require(int(b["原候选资格"]) == qualifies(c), "原资格发生变化")
            value = short_eta(usable.get(z), g)
            estimates[z] = value
            require(s["统计准入"] in ("0", "1") and s["短期预测可知"] in ("0", "1"), "短期候选布尔标志非法")
            require(int(s["统计准入"]) == (z in usable) and
                    int(s["短期预测可知"]) == (value is not None) and
                    int(s["短期达阈值纳秒"]) == (MAX if value is None else value), "短期候选预测重算不符")
        eligible = [c for c in pending if qualifies(c)]
        raw = predict(eligible, usable)
        require(tuple(int(proposal[k]) for k in ("原建议Zone", "原会改选", "原预测达阈值纳秒")) ==
                (raw[1] or 0, int(raw[2]), raw[4]), "原长期建议重算不符")
        admitted = {z:b for z,b in usable.items() if z == raw[0] or gates[z]["增长准入"] == "1"}
        gated = predict(eligible, admitted)
        require(tuple(int(proposal[k]) for k in ("准入后建议Zone", "准入后会改选")) ==
                (gated[1] or 0, int(gated[2])), "原增长准入改变")
        require((int(old["存在Greedy"]), int(old["实际Greedy"]), int(old["影子建议"]), int(old["会改选"]),
                 int(old["额外有效搬运字节"]), int(old["预测达阈值纳秒"])) ==
                (int(gated[0] is not None), gated[0] or 0, gated[1] or 0, int(gated[2]), gated[3], gated[4]), "原影子数值重算不符")
        expected = choose_short(pending, estimates)
        observed = (int(decision["实际Greedy"]) if decision["存在Greedy"] == "1" else None,
                    int(decision["建议Zone"]) if decision["存在Greedy"] == "1" else None,
                    bool(int(decision["会改选"])), int(decision["额外有效搬运字节"]),
                    int(decision["预测达阈值纳秒"]), decision["原因"])
        require(expected == observed, "短期建议、预算、排序或原因重算不符")
        require(decision["实际改选"] == old["实际改选"] == "0" and
                decision["界限纳秒"] == str(10*SECOND) and decision["预算字节"] == str(EXTRA) and
                decision["帧可用"] == old["帧可用"], "真实入口、界限或预算发生变化")
        greedy_valid = next((int(c["有效字节"]) for c in pending if int(c["Zone"]) == expected[0]), 0)
        for c in pending:
            z = int(c["Zone"]); b = blocks[z]
            if qualifies(c) and z in usable and z != expected[0]:
                observations.append(dict(轮次=round_id, 决策行号=decision["行号"], Zone=z,
                    当前块=b, 增长证据=gates[z], 有效字节=int(c["有效字节"]),
                    额外有效字节=max(0, int(c["有效字节"])-greedy_valid),
                    短期预测纳秒=estimates[z], 原预测纳秒=eta(b),
                    被短期影子建议=expected[2] and z == expected[1]))
        decisions.append(decision)
        pending, blocks, gates, shorts = [], {}, {}, {}
        proposal = decision = old = None
    require(decisions and not pending and not blocks and not gates and not shorts and
            proposal is None and decision is None and old is None, "影子轮次未闭合")
    return decisions, observations, files


def future_view(lines, samples, host_times, origin, observation, files):
    """观察截至首次维护、退役或Reset；不把维护读取或重用区读数归给旧业务。"""
    zone, start = observation["Zone"], observation["决策行号"]
    ids = {f["文件编号"] for f in files if f["轮次"] == observation["轮次"] and int(f["Zone"]) == zone}
    source_files = [f for f in files if f["轮次"] == observation["轮次"] and int(f["Zone"]) == zone]
    require(ids and sum(int(f["区内有效字节"]) for f in source_files) == observation["有效字节"],
            "候选文件身份或覆盖字节不完整")
    deaths, first = set(), None
    for number in range(start+1, len(lines)+1):
        line = lines[number-1]
        match = RR_START.search(line)
        kind = None
        if match and int(match[1]) == zone:
            kind = "独立RR开始"
        if "简单双因素实际集合：" in line:
            c = fields(line, "简单双因素实际集合：")
            if c.get("Zone") == str(zone) and c["实际选中"] == "1":
                kind = "普通ZC选择"
        if "V1后续文件退役：" in line:
            d = fields(line, "V1后续文件退役：")
            if d["文件编号"] in ids:
                deaths.add(d["文件编号"])
                if deaths == ids:
                    kind = "候选文件最后链接全部删除"
        if f"Zone 重置诊断：Zone={zone}，状态=成功，" in line:
            kind = "源Reset"
        if kind:
            first = dict(事件=kind, 行号=number, 距离决策秒=stamp(line)-stamp(lines[start-1]))
            break
    point = point_from_candidate(observation["当前块"])
    records = samples[zone]
    if first:
        cutoff = (stamp(lines[first["行号"]-1])-origin)*SECOND-20_000_000
        records = [r for r in records if host_times[(zone, r["time"])] < cutoff]
    actual = cross(records, point)
    same, broken = [point], False
    for row in records:
        if row["time"] <= point["time"]:
            continue
        if identity(row) != identity(point) or row["rc"] < same[-1]["rc"]:
            broken = True; break
        same.append(row)
    observed = None
    if actual["状态"] in ("观测到跨阈值", "决策时已达到阈值"):
        if actual["上界秒"] <= 10:
            observed = True
        elif actual["下界秒"] >= 10:
            observed = False
    if observed is None and not broken and same[-1]["time"]-point["time"] >= 10*SECOND and all(r["rc"] < 4096 for r in same):
        observed = False
    predicted = observation["短期预测纳秒"]
    forecast = None if predicted is None else predicted <= 10*SECOND
    judgement = "未知或先退役／维护，不能直接判定十秒跨阈值"
    if observed is not None and forecast is not None:
        judgement = "命中" if observed and forecast else "漏报" if observed else "误报" if forecast else "正确未报"
    out = dict(observation)
    out.update(候选文件=source_files, 后续首次事件=first, 阈值跨越观测=actual,
        十秒内跨阈值可确认=observed, 短期预测判定=judgement,
        维护或退役前最大已观测RC=max(r["rc"] for r in same),
        维护或退役前同块观测秒=(same[-1]["time"]-point["time"])/SECOND,
        真实改选收益已验证=False)
    if actual.get("原因", "").startswith("业务结束前"):
        actual["原因"] = "首次维护／退役或业务结束前未观测到跨越，不推断清理后的反事实"
    return out


def analyze(run):
    config = rows(run/"short-shadow-config.csv")
    require(len(config) == 1, "短期影子配置不唯一")
    profile = config[0]["负载配置"]
    require(profile in ("original", "maintenance-cases") and config == [{"负载配置":profile,
        "实际策略":"Greedy", "短期影子":"1", "增长采样":"1", "真实入口":"0",
        "界限秒数":"10", "额外搬运预算字节":"16777216"}], "不是预定短期影子配置")
    require(run.name == ("v1-growth-stream-greedy-20261014" if profile == "original" else "short-shadow-cases-20261014"), "结果目录不符")
    if profile == "original":
        expected_config = {"策略":"greedy", "计划业务秒数":"180", "RR阈值":"4096", "GC启动比例":"20",
            "回收预算字节":"201326592", "紧迫界限秒数":"10", "额外有效搬运预算字节":"16777216",
            "近期准入开关":"1", "观察采样毫秒":"500", "预热秒数":"0"}
    else:
        expected_config = {"实际策略":"Greedy", "计划业务秒数":"180", "RR阈值":"4096", "GC启动比例":"20",
            "回收预算字节":"201326592", "影子界限秒数":"10", "额外有效搬运预算字节":"16777216", "实际改选":"0"}
        require(rows(run/"observer-config.csv") == [{"采样间隔毫秒":"500"}], "负例采样配置改变")
        require(rows(run/"victim-profile.csv") == [{"场景配置":"maintenance-cases", "热读停止年龄秒":"65",
            "提前退役年龄秒":"66"}], "原停读／退役计划改变")
    require(rows(run/"config.csv") == [expected_config], "原运行参数改变")
    for name in ("mkfs.exit", "run.exit", "verify.exit", "runner.exit", "short-shadow-wrapper.exit"):
        require((run/name).read_text().strip() == "0", name+"未成功")
    require((run/"identity.initial.sha256").read_bytes() == (run/"identity.final.sha256").read_bytes(), "软件身份改变")
    guest = Path("/home/femu/rrzc-stream-results")/run.name
    for line in (run/"short-shadow.logs.sha256").read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        require(hashlib.sha256((run/Path(filename).relative_to(guest)).read_bytes()).hexdigest() == digest, "原始日志被修改")
    text = (run/"zenfs.run.log").read_text(); lines = text.splitlines()
    require("[ERROR]" not in text and "状态=失败" not in text and "V1真实决策：" not in text, "维护错误或开启真实入口")
    for marker in ("短期影子：已启用", "V1增长准入：已启用", "V1影子：已启用"):
        require(text.count(marker) == 1, "入口未唯一开启："+marker)
    require(set(re.findall(r"GC 检查：.*启动水平=(\d+)%", text)) == {"20"}, "GC20%未确认")
    actual_greedy = validate_decisions(lines, "valid", BUDGET)
    maintenance = parse_lines(selection_view(lines), 64)
    require(not maintenance["failed_resets"] and all(e["status"] == "完成" for e in maintenance["rr"]), "维护失败或不完整")
    operations = rows(run/"operations.csv"); traces = check_trace(operations, 180, profile)
    expected_bytes = 687865856 if profile == "original" else 654311424
    require(f"业务完整比对字节={expected_bytes}" in (run/"verify.stdout.log").read_text() and
            "填充文件首4KiB抽检=20" in (run/"verify.stdout.log").read_text(), "数据校验不完整")
    verify_log = (run/"zenfs.verify.log").read_text()
    require(not any(s in verify_log for s in ("GC 检查：", "RR 开始：", "V1影子决策：", "短期影子决策：", "[ERROR]")), "只读校验引入维护")
    decisions, observations, files = rounds(lines)
    samples, durations = read_samples(run/"recent.csv")
    require(len(durations) == 360, "原180秒500毫秒采样未完整执行")
    host_times = {(int(r["Zone"]), int(r["设备快照纳秒"])):int(r["查询结束纳秒"]) for r in rows(run/"recent.csv")}
    origins = [stamp(line)-int(m[1])/SECOND for line in lines if (m:=re.search(r"滚动更新提交：代号=\d+，经过纳秒=(\d+)", line))]
    require(origins and max(origins)-min(origins) < .020, "日志与业务时钟未稳定对齐")
    outcomes = [future_view(lines, samples, host_times, mean(origins), o, files) for o in observations]
    zc = [tuple(map(int,m.groups())) for line in lines if (m:=ZC_STATS.search(line))]
    rr = [tuple(map(int,m.groups())) for line in lines if (m:=RR_STATS.search(line))]
    require(all(r[2] == 1 for r in zc+rr), "成功源Reset数不符")
    copies = [fields(line, "V1后续迁移复制：") for line in lines if "V1后续迁移复制：" in line]
    commits = [fields(line, "V1后续映射提交：") for line in lines if "V1后续映射提交：" in line]
    require(all(c["复制成功"] == "1" for c in copies) and all(c["成功"] == "1" for c in commits) and
            Counter(c["文件编号"] for c in copies) == Counter(c["文件编号"] for c in commits), "复制或提交失败")
    moved = sum(int(c["有效字节"]) for c in copies)
    require(moved == sum(r[1] for r in zc+rr), "迁移总账不一致")
    counts, repeated = Counter(), 0
    for c in copies:
        if counts[c["文件编号"]]:
            repeated += int(c["有效字节"])
        counts[c["文件编号"]] += 1
    stops = [dict(行号=n, 原始日志=line) for n,line in enumerate(lines,1) if "受控热读停止：" in line]
    retired = [dict(行号=n, 原始日志=line) for n,line in enumerate(lines,1) if "受控文件退役完成：" in line]
    if profile == "maintenance-cases":
        require(any("代号=1，" in s["原始日志"] for s in stops) and
                any("代号=2，" in s["原始日志"] for s in stops) and
                len(retired) == 1 and "代号=2，" in retired[0]["原始日志"], "原受控停止／退役事件未实际执行")
    times = defaultdict(list)
    for r in operations:
        if int(r["代号"]) >= 0 and r["操作"] in ("读4KiB", "写1MiB"):
            require(int(r["延迟纳秒"]) >= 0, "API计时非法")
            times[r["操作"]].append(int(r["延迟纳秒"]))
    proposed = [o for o in outcomes if o["被短期影子建议"]]
    return dict(核验通过=True, 来源=str(run), 负载配置=profile, 实际策略="Greedy", 实际改选次数=0,
        短期影子轮次=len(decisions), 短期建议改选次数=sum(d["会改选"] == "1" for d in decisions),
        原增长准入后建议改选次数=sum(fields(line, "V1增长决策：")["准入后会改选"] == "1" for line in lines if "V1增长决策：" in line),
        原因分布=dict(Counter(d["原因"] for d in decisions)), 决策=decisions,
        非Greedy候选后续观测=outcomes, 建议改选候选后续观测=proposed,
        建议候选十秒预测判定=dict(Counter(o["短期预测判定"] for o in proposed)),
        建议候选首次后续事件=dict(Counter(o["后续首次事件"]["事件"] if o["后续首次事件"] else "观察结束仍未知" for o in proposed)),
        ZC次数=len(zc), RR次数=len(rr), 总维护迁移MiB=moved/(1<<20),
        ZC迁移MiB=sum(r[1] for r in zc)/(1<<20), RR迁移MiB=sum(r[1] for r in rr)/(1<<20),
        重复文件复制MiB=repeated/(1<<20), 预留替换次数=text.count("迁移预留替换："),
        预留源额外Finish次数=text.count("RR 封闭专用预留源："),
        实际受控停止事件=stops, 实际提前退役事件=retired,
        业务API耗时={k:latency(v) for k,v in times.items()}, 完整有效数据校验MiB=expected_bytes/(1<<20),
        原请求计划哈希=traces, 逐块采样批次数=len(durations), 实际Greedy核验=actual_greedy,
        终态空间=rows(run/"states.csv")[-1],
        真实改选收益已验证=False, 稳态或性能对照已验证=False,
        说明="独立180秒影子诊断。达到阈值与RR开始分开；先退役或被维护干预的候选不强行判为十秒误报。未来事件仅用于事后核验，不输入机制。")


if __name__ == "__main__":
    require(len(sys.argv) == 2, "需要本轮原始结果目录")
    print(json.dumps(analyze(Path(sys.argv[1])), ensure_ascii=False, indent=2))
