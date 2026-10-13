#!/usr/bin/env python3
"""核验同进程 RR/ZC 真实顺序和总搬运成本，不将维护分类变化当作收益。"""
import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from analyze_controlled_zc import VALID, ZC_STATS, validate_initial
from analyze_maintenance_overlap import parse_lines, relate_reclaims

MARKERS = ("准备同步结束，即将释放文件锁", "前三组已迁移，第四组仍在源区",
           "固定补读24576次完成，第四组八块各4096", "四组实际迁移完成，结束维护观察")


def marker_lines(lines):
    result = []
    for marker in MARKERS:
        found = [i for i, line in enumerate(lines, 1) if "受控同时维护：" in line and marker in line]
        if len(found) != 1:
            raise ValueError(f"同步边界缺失或重复：{marker}")
        result.append(found[0])
    if result != sorted(result):
        raise ValueError("同步边界顺序不符")
    return result


def validate_events(parsed, sources, bounds, stats):
    """每个原始源区只归入一次已完成维护，并用真实统计与成功Reset核对。"""
    start, initial_done, future_done, finish = bounds
    addresses = {s[0]: i for i, s in enumerate(sources)}
    kinds = {}
    for event in parsed["rr"]:
        index = addresses.get(event["zone"])
        if index is None or index in kinds:
            raise ValueError("RR源区未知或原始源区重复刷新")
        diag = event.get("diagnostic") or {}
        if (event["status"] != "完成" or event["moved"] != VALID[index]
                or event["valid"] != VALID[index] or event["resets"] != 1 or event["tail"] != 0
                or not diag.get("sst_eligible") or not diag.get("stable") or diag.get("generation_current") != 0):
            raise ValueError("RR成本、覆盖资格或源代际不符合已完成原始源区维护")
        lower, upper = (future_done, finish) if index == 3 else (start, initial_done)
        if not lower < event["line"] < event["end_line"] < upper:
            raise ValueError("RR不在对应的初始/固定补读观察窗口内")
        if event["at"] != (8 if index == 3 else 1):
            raise ValueError("RR实际触发风险与固定读取计划不符")
        kinds[index] = "RR"
    selected = {}
    for item in parsed["candidates"]:
        if not start < item["line"] < finish or not item["selected"]:
            continue
        index = addresses.get(item["zone"])
        diag = item.get("diagnostic") or {}
        if (index is None or index in kinds or item["valid"] != VALID[index]
                or not diag.get("sst_eligible") or not diag.get("stable") or diag.get("generation_current") != 0):
            raise ValueError("实际ZC选中源区与原始源区或覆盖资格不符")
        selected.setdefault(index, item)
    kinds.update({i: "ZC" for i in selected})
    if set(kinds) != set(range(4)):
        raise ValueError("四组原始源区未全部对应实际维护")
    zc_bytes = sum(s[1] for s in stats)
    zc_resets = sum(s[2] for s in stats)
    if zc_bytes != sum(VALID[i] for i in selected) or zc_resets != len(selected):
        raise ValueError("ZC真实迁移/重置统计与选中源区不符")
    for zone in addresses:
        resets = [b for b in parsed["barriers"] if b["zone"] == zone
                  and start < b["line"] < finish and b["reason"].startswith("主机成功 Reset")]
        if len(resets) != 1 or resets[0]["reason"] != "主机成功 Reset：代际 0/1":
            raise ValueError("原始源区缺少唯一的0→1成功重置证据")
    rr_bytes = sum(r["moved"] for r in parsed["rr"])
    if rr_bytes + zc_bytes != sum(VALID):
        raise ValueError("总维护搬运量不等于四组已完成迁移的数据量")
    return kinds, selected, zc_bytes, rr_bytes


def analyze(run):
    for phase in ("mkfs", "runner", "concurrent", "verify"):
        if (run / f"{phase}.exit").read_text().strip() != "0":
            raise ValueError(f"未完成：{phase}")
    identity = (run / "identity.initial.sha256").read_bytes()
    if identity != (run / "identity.final.sha256").read_bytes() or len(identity.splitlines()) != 6:
        raise ValueError("前后六项软件身份不一致")
    for line in (run / "logs.sha256").read_text().splitlines():
        digest, guest = line.split()
        if hashlib.sha256((run / Path(guest).name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"归档文件哈希不一致：{guest}")
    initial_zones = json.loads((run / "zones.initial.json").read_text())
    if initial_zones["nr_zones"] != 32 or any(z["state"] != "EMPTY" for z in initial_zones["zone_list"]):
        raise ValueError("实验设备不是初始32个全空Zone")
    sources = [tuple(map(int, line.split())) for line in (run / "sources.tsv").read_text().splitlines()]
    with (run / "initial.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    validate_initial(rows, sources)
    with (run / "future.csv").open() as handle:
        future = list(csv.DictReader(handle))
    if len(future) != 1 or {k: int(v) for k, v in future[0].items()} != {
            "新增4KiB读次数": 24576, "读数总和": 32768, "最大读数": 4096, "达阈值块": 8}:
        raise ValueError("固定补读实际记录不符")
    native = (run / "zenfs.concurrent.log").read_text()
    lines = native.splitlines()
    parsed = parse_lines(lines, 64)
    bounds = marker_lines(lines)
    verification = (run / "zenfs.verify.log").read_text()
    verify_parsed = parse_lines(verification.splitlines(), 64)
    if ("[ERROR]" in native + verification or parsed["failed_resets"] or verify_parsed["failed_resets"]
            or verify_parsed["rr"] or ZC_STATS.search(verification)):
        raise ValueError("出现维护错误或校验阶段额外维护")
    checks = re.findall(r"GC 检查：.*启动水平=(\d+)%", native)
    if not checks or set(checks) != {"20"}:
        raise ValueError("未确认原GC20%启动策略")
    stats = [tuple(map(int, m)) for m in ZC_STATS.findall(native)]
    kinds, selected, zc_bytes, rr_bytes = validate_events(parsed, sources, bounds, stats)
    if "全部320MiB字节比对通过" not in (run / "verify.stdout.log").read_text():
        raise ValueError("缺少320MiB完整字节比对通过记录")
    post_candidates = [c for c in parsed["candidates"] if c["line"] > bounds[0]]
    if not parsed["rr"] or not post_candidates or parsed["rr"][0]["end_line"] >= post_candidates[0]["line"]:
        raise ValueError("没有实际RR完成先于释放锁后首次ZC候选的证据")
    candidate_rows = [{"组号": i, "候选行号": c["line"], "空闲比例": c["free"], "无效比例门槛": c["gate"]}
                      for i, c in sorted(selected.items())]
    relations = [{"RR源Zone": r["rr"]["zone"], "RR开始行号": r["rr"]["line"],
                  "最近此前候选行号": r["candidate"]["line"] if r["candidate"] else None,
                  "关联口径": r["status"]} for r in relate_reclaims(parsed)]
    return {"来源": str(run), "软件身份": identity.decode().splitlines(), "初始四格": rows,
            "四组实际维护类型": [{"组号": i, "维护类型": kinds[i], "实际迁移字节": VALID[i]} for i in range(4)],
            "实际ZC选中": candidate_rows, "ZC实际轮数": len(stats), "ZC实际搬运字节": zc_bytes,
            "RR实际次数": len(parsed["rr"]), "RR实际搬运字节": rr_bytes,
            "总维护搬运字节": zc_bytes + rr_bytes, "原始源区成功重置数": 4,
            "实际RR事件": parsed["rr"], "RR此前候选关联": relations,
            "首次实际RR结束行号": parsed["rr"][0]["end_line"], "释放锁后首次ZC候选行号": post_candidates[0]["line"],
            "同步边界行号": bounds, "320MiB字节比对": "通过"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="尚不存在的输出文件")
    args = parser.parse_args()
    try:
        runs = [analyze(run) for run in args.runs]
        if len({tuple(run["软件身份"]) for run in runs}) != 1:
            raise ValueError("各轮软件身份不一致")
        result = {"口径限制": ["RR/ZC一个观察进程始终开启；原RR先于GC顺序不变。",
                              "准备及固定补读有文件写锁同步，不是无锁异步业务或性能对照。",
                              "载荷不是RocksDB SST，阈值4096仅为探索值。",
                              "校验另进程关闭RR；不同进程的Reset代际不拼接。",
                              "维护分类变化不等于总成本降低，未实现RR-aware选区。"], "真实运行": runs}
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"核验失败：{error}\n")
    print(f"同时维护核验通过：{args.output}")


if __name__ == "__main__":
    main()
