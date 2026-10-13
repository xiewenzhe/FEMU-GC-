#!/usr/bin/env python3
"""核验近阈值和真实数据失效时序，仅报告实际成本，不推导选区收益。"""
import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from analyze_controlled_zc import VALID, ZC_STATS
from analyze_maintenance_overlap import parse_lines, relate_reclaims


def validate_initial(rows, sources):
    if len(rows) != 4 or len(sources) != 4 or len({s[0] for s in sources}) != 4:
        raise ValueError("近阈值四格初始记录或不同源区不完整")
    for i, row in enumerate(rows):
        expected = {"组号": i, "Zone字节地址": sources[i][0], "有效字节": VALID[i],
                    "真实删除字节": (256 << 20) - VALID[i], "累计4KiB读次数": 3584,
                    "最大读数": 448 if i % 2 else 3584, "读数总和": 3584, "达阈值块": 0, "空闲比例": 10}
        if sources[i][1] != VALID[i] or row["读取分布"] != ("八块分散" if i % 2 else "单块集中"):
            raise ValueError("组标签或初始有效量不符")
        if any(int(row[k]) != v for k, v in expected.items()):
            raise ValueError(f"第{i}组初始风险、有效量或空间状态不符")


def validate_future(rows, sources, before):
    if len(rows) != 4:
        raise ValueError("固定补读记录不是四组")
    for i, row in enumerate(rows):
        if int(row["组号"]) != i or int(row["新增4KiB读次数"]) != 512:
            raise ValueError("各组补读次数或标签不符")
        if i >= 2:
            expected = {"读取Zone": sources[i][0], "读数总和": 4096,
                        "最大读数": 512 if i % 2 else 4096, "达阈值块": 0 if i % 2 else 1,
                        "区有效字节": (64 if before else 128) << 20}
            if any(int(row[k]) != v for k, v in expected.items()):
                raise ValueError("大组补读风险或失效时序对应有效量不符")
        elif int(row["读取Zone"]) == sources[i][0] or int(row["达阈值块"]) != 0:
            raise ValueError("小组未迁移或补读后达到RR阈值")


def boundary(lines, text):
    hits = [i for i, line in enumerate(lines, 1) if "受控近阈值：" in line and text in line]
    if len(hits) != 1:
        raise ValueError(f"边界缺失或重复：{text}")
    return hits[0]


def analyze(run):
    phases = [p for p in ("near-before", "near-after") if (run / f"{p}.exit").exists()]
    if len(phases) != 1:
        raise ValueError("失效时序身份不明确")
    phase = phases[0]
    before = phase == "near-before"
    for p in ("mkfs", "runner", phase, "near-verify"):
        if (run / f"{p}.exit").read_text().strip() != "0":
            raise ValueError(f"未完成：{p}")
    identity = (run / "identity.initial.sha256").read_bytes()
    if identity != (run / "identity.final.sha256").read_bytes() or len(identity.splitlines()) != 6:
        raise ValueError("前后软件身份不一致")
    for line in (run / "logs.sha256").read_text().splitlines():
        digest, guest = line.split()
        if hashlib.sha256((run / Path(guest).name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"归档哈希不一致：{guest}")
    zones = json.loads((run / "zones.initial.json").read_text())
    if zones["nr_zones"] != 32 or len(zones["zone_list"]) != 32 or any(z["state"] != "EMPTY" for z in zones["zone_list"]):
        raise ValueError("初始设备不是32个全空Zone")
    sources = [tuple(map(int, line.split())) for line in (run / "sources.tsv").read_text().splitlines()]
    with (run / "initial.csv").open() as f:
        rows = list(csv.DictReader(f))
    validate_initial(rows, sources)
    with (run / "future.csv").open() as f:
        future = list(csv.DictReader(f))
    validate_future(future, sources, before)
    native = (run / f"zenfs.{phase}.log").read_text()
    lines = native.splitlines()
    parsed = parse_lines(lines, 64)
    verify = (run / "zenfs.near-verify.log").read_text()
    vp = parse_lines(verify.splitlines(), 64)
    if "[ERROR]" in native + verify or parsed["failed_resets"] or vp["failed_resets"] or vp["rr"] or ZC_STATS.search(verify):
        raise ValueError("出现错误、失败Reset或校验阶段额外维护")
    start = boundary(lines, "初始3584次读记录完成")
    zc_done = boundary(lines, "两个小组已由真实维护迁移")
    deletion = boundary(lines, "真实失效删除完成")
    pulse = boundary(lines, "各组固定补读512次完成")
    rr_done = boundary(lines, "集中大组RR和源重置已观察完成")
    end = boundary(lines, "追加12秒观察结束")
    order = (start, zc_done, deletion, pulse, rr_done, end) if before else (start, zc_done, pulse, rr_done, deletion, end)
    if list(order) != sorted(order):
        raise ValueError("初始维护、补读、真实失效和RR时序不符")
    if f"时序={'RR前' if before else 'RR后'}" not in lines[deletion - 1]:
        raise ValueError("删除时序标记不符")
    stats = [tuple(map(int, m)) for m in ZC_STATS.findall(native)]
    if len(stats) != 1 or stats[0][1:] != (64 << 20, 2):
        raise ValueError("实际ZC不是一轮64MiB/两源重置")
    if len(parsed["rr"]) != 1:
        raise ValueError("不是一次实际后续RR")
    rr = parsed["rr"][0]
    cost = (64 if before else 128) << 20
    diag = rr.get("diagnostic") or {}
    if (rr["zone"] != sources[2][0] or rr["valid"] != cost or rr["moved"] != cost or rr["resets"] != 1
            or rr["at"] != 1 or rr["tail"] != 0 or rr["status"] != "完成"
            or not pulse < rr["line"] < rr["end_line"] < rr_done
            or not diag.get("sst_eligible") or not diag.get("stable") or diag.get("generation_current") != 0):
        raise ValueError("实际RR窗口、成本、资格或原始源代际不符")
    candidates = []
    for i, (zone, size) in enumerate(sources):
        matches = [c for c in parsed["candidates"] if c["zone"] == zone and start < c["line"] < zc_done
                   and c["valid"] == size and c["selected"] == int(i < 2) and c["maximum"] == (448 if i % 2 else 3584)
                   and c["at"] == 0 and c["free"] == 10 and c["gate"] == 70
                   and c.get("diagnostic") and c["diagnostic"]["sst_eligible"] and c["diagnostic"]["stable"]
                   and c["diagnostic"]["generation_current"] == 0]
        if not matches:
            raise ValueError(f"第{i}组没有未到阈值时的真实10%/70%候选")
        candidates.append(matches[0])
        resets = [b for b in parsed["barriers"] if b["zone"] == zone and b["reason"].startswith("主机成功 Reset")]
        if len(resets) != int(i < 3) or any(b["reason"] != "主机成功 Reset：代际 0/1" for b in resets):
            raise ValueError("原始源区成功Reset分布不符")
    if len(re.findall(r"DeleteFile: /controlled-zc/case[23]\.expire\.sst", native)) != 2:
        raise ValueError("没有两个过期文件的真实删除记录")
    if not any(c["zone"] == sources[3][0] and c["valid"] == 64 << 20 and c["selected"] == 0
               and c["line"] > deletion and c["at"] == 0 for c in parsed["candidates"]):
        raise ValueError("缺少失效后有限窗口内低风险大组未选中的真实观测")
    if set(re.findall(r"GC 检查：.*启动水平=(\d+)%", native)) != {"20"}:
        raise ValueError("未确认原GC20%")
    if "剩余四组192MiB字节比对通过" not in (run / "near-verify.stdout.log").read_text():
        raise ValueError("缺少剩余192MiB字节校验通过记录")
    relation = relate_reclaims(parsed)[0]
    return {"来源": str(run), "时序": "RR前失效" if before else "RR后失效", "初始四格": rows,
            "固定补读": future, "初始真实候选": candidates, "ZC实际搬运字节": 64 << 20,
            "RR实际搬运字节": cost, "RR实际次数": 1, "总维护搬运字节": (64 << 20) + cost,
            "实际RR": rr, "RR最近候选关联口径": relation["status"],
            "RR最近候选有效字节": relation["candidate"]["valid"] if relation["candidate"] else None,
            "原始源区成功Reset数": 3, "剩余192MiB校验": "通过", "软件身份": identity.decode().splitlines()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        runs = [analyze(p) for p in args.runs]
        if len({tuple(r["软件身份"]) for r in runs}) != 1 or any(r["初始四格"] != runs[0]["初始四格"] for r in runs):
            raise ValueError("各轮软件或初始条件不一致")
        with args.output.open("x", encoding="utf-8") as f:
            json.dump({"口径限制": ["比较的是失效时序，不是不同victim策略；未计算可避免成本。",
                                  "RR/ZC始终开启，准备/补读有写锁同步，非性能对照。",
                                  "阈值4096仅为探索值；模式文件不是RocksDB SST。",
                                  "只校验剩余192MiB；未完整校验填充及已经删除的文件。",
                                  "未观察到RR仅指有限窗口；同一Reset代际不保证有效量不变。"],
                       "真实运行": runs}, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"核验失败：{error}\n")
    print(f"近阈值/失效时序核验通过：{args.output}")


if __name__ == "__main__":
    main()
