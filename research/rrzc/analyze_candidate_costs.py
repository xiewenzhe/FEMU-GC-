#!/usr/bin/env python3
"""从已核验日志整理候选成本；组合枚举只是静态预算，不是替代策略实验。"""
import argparse
import csv
import hashlib
import itertools
import json
from pathlib import Path

from analyze_controlled_near import analyze, boundary
from analyze_maintenance_overlap import make_relation, parse_lines

ZONE_BYTES = 256 << 20


def enumerate_batches(rows):
    """使用源区无效字节作同等空间参考，不把Reset个数当成相同净回收量。"""
    if not 1 <= len(rows) <= 16 or len({r["组号"] for r in rows}) != len(rows):
        raise ValueError("组合范围为空、过大或组号重复")
    for row in rows:
        if not 0 < row["当前有效字节"] < ZONE_BYTES or row["当前无效字节"] != ZONE_BYTES - row["当前有效字节"]:
            raise ValueError("不是有实际有效/无效空间的256MiB满区")
        if row["基线路径窗口内同代RR字节"] < 0:
            raise ValueError("基线RR记录不能为负")
    baseline = [r for r in rows if r["原门槛选中"]]
    if not baseline:
        raise ValueError("没有实际原策略选中源区")
    required = sum(r["当前无效字节"] for r in baseline)
    base_cost = sum(r["当前有效字节"] for r in baseline)
    plans = []
    for count in range(1, len(rows) + 1):
        for chosen in itertools.combinations(rows, count):
            garbage = sum(r["当前无效字节"] for r in chosen)
            cost = sum(r["当前有效字节"] for r in chosen)
            credit = sum(r["基线路径窗口内同代RR字节"] for r in chosen)
            meets = garbage >= required
            plans.append({"组合组号": [r["组号"] for r in chosen],
                          "身份": "实际基线选中集合" if list(chosen) == baseline else "未执行的假设组合",
                          "保持原门槛可入选": all(r["原门槛选中"] for r in chosen),
                          "源Reset总容量预算字节": count * ZONE_BYTES,
                          "当前有效量迁移预算字节": cost, "当前无效空间预算字节": garbage,
                          "满足基线无效空间预算": meets,
                          "增量当前迁移预算字节": cost - base_cost,
                          "借用基线已观测RR的参考抵扣字节": credit,
                          "静态差额字节（非实测收益）": cost - base_cost - credit if meets else None})
    return {"基线组号": [r["组号"] for r in baseline], "基线当前迁移预算字节": base_cost,
            "同等空间参考（基线当前无效字节）": required, "假设组合": plans}


def build_run(path):
    verified = analyze(path)
    phase = "near-before" if verified["时序"] == "RR前失效" else "near-after"
    logfile = path / f"zenfs.{phase}.log"
    data = logfile.read_bytes()
    lines = data.decode().splitlines()
    parsed = parse_lines(lines, 64)
    end = boundary(lines, "追加12秒观察结束")
    rows = []
    for i, candidate in enumerate(verified["初始真实候选"]):
        diag = candidate.get("diagnostic") or {}
        if not diag.get("stable") or not diag.get("sst_eligible") or diag.get("wp") - candidate["zone"] != ZONE_BYTES:
            raise ValueError("候选缺少稳定满区/完整覆盖诊断")
        if candidate["invalid"] != 100 - 100 * candidate["valid"] // ZONE_BYTES:
            raise ValueError("候选近似无效比例与真实容量不符")
        following = [r for r in parsed["rr"] if r["zone"] == candidate["zone"]
                     and candidate["line"] < r["line"] < end]
        relations = [make_relation(candidate, rr, parsed["barriers"], parsed["failed_resets"]) for rr in following]
        same = [r for r in relations if r["status"] == "同一主机 Reset 代际（非原子数据快照）"]
        if len(same) > 1 or any(r["rr"]["status"] != "完成" for r in same):
            raise ValueError("同代RR不是唯一已完成维护")
        event = same[0]["rr"] if same else None
        observed = event["moved"] if event else 0
        if candidate["selected"]:
            description = "实际已被ZC维护；未清理时的RR成本未知"
        elif event:
            description = "同代后续RR实测；不是提前ZC后的可避免成本"
        else:
            description = "基线路径有限窗口未观察到同代RR；不表示永久为零"
        rows.append({"时序": verified["时序"], "组号": i, "源Zone": candidate["zone"],
                     "当前有效字节": candidate["valid"], "当前无效字节": ZONE_BYTES - candidate["valid"],
                     "源Reset总容量预算字节": ZONE_BYTES, "近似无效比例": candidate["invalid"],
                     "候选空闲比例": candidate["free"], "原无效比例门槛": candidate["gate"],
                     "原门槛选中": bool(candidate["selected"]), "最大读数": candidate["maximum"],
                     "读数总和": candidate["total"], "读压力阈值": candidate["threshold"],
                     "最高读数距阈值": max(0, candidate["threshold"] - candidate["maximum"]),
                     "80%风险块": candidate["ge80"], "90%风险块": candidate["ge90"], "达阈值块": candidate["at"],
                     "主机Reset代际": diag["generation_current"], "候选行号": candidate["line"],
                     "候选时间（客机UTC）": candidate["time"],
                     "基线路径窗口内同代RR字节": observed, "RR开始有效字节": event["valid"] if event else None,
                     "RR开始行号": event["line"] if event else None,
                     "RR结束行号": event["end_line"] if event else None,
                     "候选至RR间隔秒（本轮观察）": same[0]["delta"] if same else None,
                     "RR成本口径": description, "提前清理的可避免RR字节": None,
                     "未清理的反事实RR字节": None})
    budgets = enumerate_batches(rows)
    if budgets["基线当前迁移预算字节"] != verified["ZC实际搬运字节"]:
        raise ValueError("选中集合当前有效量与实际ZC统计不符")
    if sum(r["基线路径窗口内同代RR字节"] for r in rows) != verified["RR实际搬运字节"]:
        raise ValueError("逐候选RR记录与真实运行统计不符")
    eligible = [p for p in budgets["假设组合"] if p["保持原门槛可入选"] and p["满足基线无效空间预算"]]
    relaxed = [p for p in budgets["假设组合"] if p["满足基线无效空间预算"] and p["身份"] != "实际基线选中集合"]
    return {"来源": str(path), "时序": verified["时序"], "原生日志SHA256": hashlib.sha256(data).hexdigest(),
            "软件身份": verified["软件身份"], "初始条件": verified["初始四格"], "候选成本记录": rows,
            "实际基线ZC字节": verified["ZC实际搬运字节"], "实际基线RR字节": verified["RR实际搬运字节"],
            "实际基线总维护字节": verified["总维护搬运字节"], "静态组合预算": budgets,
            "保持原门槛且达到空间参考的集合数": len(eligible),
            "放宽准入假设下非基线集合最小静态差额": min(p["静态差额字节（非实测收益）"] for p in relaxed) if relaxed else None}


def write_reports(output, runs):
    if output.exists():
        raise ValueError(f"输出目录已存在，拒绝覆盖：{output}")
    if not runs:
        raise ValueError("没有核验通过的输入")
    # 不把不同失效时序当成公平策略对照；每轮独立计算组合成本。
    if len({tuple(r["软件身份"]) for r in runs}) != 1 or any(r["初始条件"] != runs[0]["初始条件"] for r in runs):
        raise ValueError("各轮软件身份或初始条件不同")
    output.mkdir(parents=True)
    notes = ["只读重核验历史原日志，未运行替代victim策略，也不是预测模型或最终选区公式。",
             "只枚举四个测试源区，忽略其他填充区；当前代码批量选中所有过空间门槛者。",
             "当前有效量是迁移载荷预算，不是完整物理I/O成本；当前无效字节是静态净空间参考。",
             "源Reset总容量不等于净空闲增量；预留区、Finish、目标容量和运行时准入未建模。",
             "同代RR字节仅为基线路径已完成记录；零表示窗口无记录，未清理和提前清理的反事实均未知。",
             "假设组合不一定通过原空间门槛；静态参考抵扣借用基线RR记录，不是真实可避免成本。",
             "静态差额假设迁移发生在原初始观察点、所选源区相关基线RR可全部抵扣且没有新增维护。",
             "候选至RR间隔仅为本轮时序观测；有写锁同步和10秒轮询，不能当成预测到阈值时间。",
             "两个失效时序独立核算，不混用RR成本；阈值4096和模式文件仍非正式QLC/数据库实验。"]
    with (output / "候选成本核验.json").open("x", encoding="utf-8") as f:
        json.dump({"口径限制": notes, "核验运行": runs}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with (output / "候选成本.csv").open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(runs[0]["候选成本记录"][0]))
        writer.writeheader()
        for run in runs:
            writer.writerows(run["候选成本记录"])
    fields = ["时序"] + list(runs[0]["静态组合预算"]["假设组合"][0])
    with (output / "未执行组合的静态预算.csv").open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for run in runs:
            for plan in run["静态组合预算"]["假设组合"]:
                writer.writerow({"时序": run["时序"], **plan})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="尚不存在的新目录")
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("输出目录已存在，拒绝覆盖")
        write_reports(args.output, [build_run(p) for p in args.runs])
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"成本核验失败：{error}\n")
    print(f"候选成本与静态空间预算核验完成：{args.output}；不是替代选区实验。")


if __name__ == "__main__":
    main()
