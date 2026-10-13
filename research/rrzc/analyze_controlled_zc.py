#!/usr/bin/env python3
"""核验四格分阶段 ZC/RR 场景，不跨进程拼接代际，不计算选区收益。"""
import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from analyze_maintenance_overlap import parse_lines

PHASES = ("zc", "rr-first", "future", "rr-second", "verify")
VALID = (32 << 20, 32 << 20, 128 << 20, 128 << 20)
ZC_STATS = re.compile(r"ZC 统计：轮次 (\d+)，本轮迁移 (\d+) 字节，本轮重置 (\d+) 个 Zone")


def validate_initial(rows, sources):
    """固定8192次初始读；分散八块此时各1024，低于探索阈值4096。"""
    if len(rows) != 4 or len(sources) != 4 or len({s[0] for s in sources}) != 4:
        raise ValueError("四组初始记录或不同源区清单不完整")
    for index, row in enumerate(rows):
        expected = {"组号": index, "Zone字节地址": sources[index][0], "有效字节": VALID[index],
                    "真实删除字节": (256 << 20) - VALID[index], "累计4KiB读次数": 8192,
                    "最大读数": 1024 if index % 2 else 8192, "读数总和": 8192,
                    "达阈值块": 0 if index % 2 else 1, "空闲比例": 10}
        if sources[index][1] != VALID[index] or row["读取分布"] != ("八块分散" if index % 2 else "单块集中"):
            raise ValueError("组标签或源区有效量不符")
        for key, value in expected.items():
            if int(row[key]) != value:
                raise ValueError(f"第{index}组初始 {key} 不符合计划")


def analyze(run):
    for phase in PHASES:
        if (run / f"{phase}.exit").read_text().strip() != "0":
            raise ValueError(f"阶段未完成：{phase}")
    if (run / "runner.exit").read_text().strip() != "0":
        raise ValueError("执行脚本未完成")
    identity = (run / "identity.initial.sha256").read_bytes()
    if identity != (run / "identity.final.sha256").read_bytes():
        raise ValueError("前后六项软件身份不一致")
    for line in (run / "logs.sha256").read_text().splitlines():
        digest, guest = line.split()
        if hashlib.sha256((run / Path(guest).name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"原始日志哈希不一致：{guest}")
    sources = [tuple(map(int, line.split())) for line in (run / "sources.tsv").read_text().splitlines()]
    with (run / "initial.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    validate_initial(rows, sources)
    native, parsed = {}, {}
    for phase in PHASES:
        native[phase] = (run / f"zenfs.{phase}.log").read_text()
        parsed[phase] = parse_lines(native[phase].splitlines(), 64)
        if "[ERROR]" in native[phase] or parsed[phase]["failed_resets"]:
            raise ValueError(f"阶段含维护错误或失败重置：{phase}")
        if phase not in ("rr-first", "rr-second") and parsed[phase]["rr"]:
            raise ValueError(f"RR关闭阶段出现实际RR：{phase}")
        if phase != "zc" and ZC_STATS.search(native[phase]):
            raise ValueError(f"非ZC观察阶段出现额外ZC：{phase}")
    stats = [tuple(map(int, match)) for match in ZC_STATS.findall(native["zc"])]
    if sum(s[1] for s in stats) != 64 << 20 or sum(s[2] for s in stats) != 2:
        raise ValueError("真实ZC搬运量或源重置数不符合两个32MiB组")
    observations = []
    for index, (zone, _) in enumerate(sources):
        candidates = [c for c in parsed["zc"]["candidates"] if c["zone"] == zone and c["valid"] == VALID[index]
                      and c["diagnostic"] and c["diagnostic"]["sst_eligible"] and c["diagnostic"]["stable"]]
        expected_selected = int(index < 2)
        matching = [c for c in candidates if c["selected"] == expected_selected]
        if not matching:
            raise ValueError(f"第{index}组没有预期且具覆盖资格的实际ZC观测")
        item = matching[0]
        observations.append({"组号": index, "源Zone": zone, "有效字节": item["valid"],
                             "最大读数": item["maximum"], "达阈值块": item["at"], "原策略选中": item["selected"],
                             "当时空闲比例": item["free"], "原无效比例门槛": item["gate"],
                             "候选行号": item["line"], "候选时间（客机UTC）": item["time"]})
    reclaims = []
    for phase, index, at in (("rr-first", 2, 1), ("rr-second", 3, 8)):
        events = parsed[phase]["rr"]
        if len(events) != 1:
            raise ValueError(f"{phase}不是一次实际RR")
        item = events[0]
        if (item["zone"] != sources[index][0] or item["at"] != at or item["status"] != "完成"
                or item["valid"] != VALID[index] or item["moved"] != VALID[index] or item["resets"] != 1):
            raise ValueError(f"{phase}源区、风险或实际成本不符合计划")
        reclaims.append({"阶段": phase, "组号": index, "达阈值块": at,
                         "实际搬运字节": item["moved"], "状态": item["status"]})
    if "新增24576次，累计32768次，八块各4096读" not in (run / "future.stdout.log").read_text():
        raise ValueError("缺少固定未来读的实际通过记录")
    if "全部320MiB字节比对通过" not in (run / "verify.stdout.log").read_text():
        raise ValueError("缺少四组完整字节比对通过记录")
    return {"来源": str(run), "初始四格": rows, "原策略实际候选": observations,
            "ZC实际轮数": len(stats), "ZC实际搬运字节": sum(s[1] for s in stats),
            "ZC实际源重置数": sum(s[2] for s in stats), "实际后续RR": reclaims,
            "完整320MiB字节比对": "通过", "软件身份": identity.decode().splitlines(),
            "阶段日志哈希": {p: hashlib.sha256(native[p].encode()).hexdigest() for p in PHASES}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path, help="各独立轮次的完整结果目录")
    parser.add_argument("--output", type=Path, required=True, help="尚不存在的新汇总文件")
    args = parser.parse_args()
    try:
        runs = [analyze(run) for run in args.runs]
        if len({tuple(run["软件身份"]) for run in runs}) != 1:
            raise ValueError("各轮软件身份不一致")
        result = {"口径限制": ["阈值4096仅为探索值；载荷不是RocksDB SST。",
                              "准备使用文件写锁同步，释放后由原维护线程自然处理。",
                              "RR按阶段启停，不是ZC/RR同时运行或公平性能对照。",
                              "各进程代际从零开始，不能拼接日志按代际关联。",
                              "未实现RR-aware选区，未计算可避免成本。"], "真实运行": runs}
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"核验失败：{error}\n")
    print(f"四格场景核验通过：{args.output}")


if __name__ == "__main__":
    main()
