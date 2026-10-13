#!/usr/bin/env python3
"""只读核验三档受控测试，不推导真实 QLC 阈值或选区收益。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from analyze_maintenance_overlap import parse_lines


def validate_pressure(rows, threshold, zones):
    """按预定读取计划核对汇总；不声称恢复未上报的逐块计数向量。"""
    points = (0, threshold // 4, threshold // 2, threshold * 4 // 5, threshold * 9 // 10, threshold)
    if len(rows) != 12:
        raise ValueError("读压力记录应为六个阶段、每阶段两个区")
    for index, row in enumerate(rows):
        kind = index % 2
        count = points[index // 2]
        maximum = count if kind else 8 * count
        blocks = 8 if kind else 1
        expected = {
            "阶段轮数": count, "Zone字节地址": zones[kind],
            "有效字节": (128 if kind else 32) * (1 << 20),
            "累计4KiB读次数": count * 8, "最大读数": maximum, "读数总和": count * 8,
            "80%风险块": blocks if maximum >= threshold - threshold // 5 else 0,
            "90%风险块": blocks if maximum >= threshold - threshold // 10 else 0,
            "达阈值块": blocks if maximum >= threshold else 0,
        }
        if row["分布"] != ("八热块" if kind else "单热块"):
            raise ValueError("读压力分布标签与计划不符")
        for key, value in expected.items():
            if int(row[key]) != value:
                raise ValueError(f"第 {index + 1} 条压力记录的 {key} 不符合计划")


def analyze(root, threshold):
    run = root / str(threshold) / f"rrzc-controlled-{threshold}"
    for phase in ("prepare", "rr", "verify"):
        if (run / f"{phase}.exit").read_text().strip() != "0":
            raise ValueError(f"阈值 {threshold} 的 {phase} 阶段未成功")
    identity = (run / "identity.initial.sha256").read_bytes()
    if identity != (run / "identity.final.sha256").read_bytes():
        raise ValueError("阶段前后六项身份不一致")
    for line in (run / "logs.sha256").read_text().splitlines():
        digest, guest = line.split()
        if hashlib.sha256((run / Path(guest).name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"归档哈希不一致：{guest}")
    sources = [tuple(map(int, line.split())) for line in (run / "sources.tsv").read_text().splitlines()]
    if len(sources) != 2 or sources[0][0] == sources[1][0]:
        raise ValueError("两个测试源区清单不符合计划")
    zones = [s[0] for s in sources]
    with (run / "pressure.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    validate_pressure(rows, threshold, zones)
    parsed = parse_lines((run / "zenfs.rr.log").read_text().splitlines(), 64)
    if len(parsed["rr"]) != 2 or parsed["batches"] or parsed["failed_resets"]:
        raise ValueError("受控 RR 记录不是两次完成或混入了 ZC/失败重置")
    events = {item["zone"]: item for item in parsed["rr"]}
    for index, (zone, size) in enumerate(sources):
        event = events[zone]
        if (size != (32 if index == 0 else 128) * (1 << 20) or event["status"] != "完成"
                or event["at"] != (1 if index == 0 else 8) or event["valid"] != size
                or event["moved"] != size or event["resets"] != 1):
            raise ValueError("实际 RR 风险块数、有效量或搬运结果不符合载荷")
    for phase in ("prepare", "rr", "verify"):
        native = (run / f"zenfs.{phase}.log").read_text()
        if "[ERROR]" in native or "ZC 统计：" in native or (phase != "rr" and "RR 开始：" in native):
            raise ValueError("阶段混入错误、ZC 或不应启用的 RR")
    if "全部160MiB字节比对通过" not in (run / "verify.stdout.log").read_text():
        raise ValueError("缺少完整字节比对通过记录")
    return {"探索阈值": threshold, "每区受控4KiB读次数": 8 * threshold,
            "最终达阈值块": [1, 8], "有效字节": [s[1] for s in sources],
            "RR完成数": 2, "实际RR搬运字节": [events[z]["moved"] for z in zones],
            "ZC轮数": 0, "完整字节比对": "通过", "程序源码身份": identity.decode().splitlines(),
            "原始RR日志哈希": hashlib.sha256((run / "zenfs.rr.log").read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="已归档的三档结果根目录")
    parser.add_argument("--output", type=Path, required=True, help="尚不存在的新汇总文件")
    args = parser.parse_args()
    try:
        runs = [analyze(args.root, value) for value in (256, 1024, 4096)]
        if len({tuple(r["程序源码身份"]) for r in runs}) != 1:
            raise ValueError("三档实验没有使用同一程序和生产源码")
        result = {"口径": ["参数为预先指定的探索值，不是校准后的 QLC 阈值。",
                           "每档读取次数随阈值变化，不能据此比较固定负载下 RR 频率。",
                           "有效量与热块数同时变化，不能把搬运差异归因于热块数。",
                           "测试文件不是 RocksDB SST；未触发 ZC，不证明选区收益。"], "真实实验": runs}
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"核验失败：{error}\n")
    print(f"三档受控结果核验通过：{args.output}")


if __name__ == "__main__":
    main()
