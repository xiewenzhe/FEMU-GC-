#!/usr/bin/env python3
"""只读核验本轮真实逐块短测记录；不运行负载，不更改或补全原始数据。"""
import csv
import json
from pathlib import Path
import sys

SECOND = 1_000_000_000
STAGES = ["初始查询", "块A二十次", "最大块切换到B", "十秒窗口到期",
          "块B再七次", "早期事件离开三十秒", "Reset后", "Reset十一秒", "Reset三十一秒"]


def require(value, why):
    if not value:
        raise ValueError(why)


def read(directory, phase):
    result = {}
    with (directory / f"{phase}.log").open(encoding="utf-8") as stream:
        for row in csv.reader(stream):
            if not row or row[0] != "观测":
                continue
            require(len(row) == 18, "观测字段长度不符")
            stage, values = row[1], [int(x) for x in row[2:]]
            zone, address, session_hi, session_lo, generation, block_id, ch, lun, plane, block, now, born, rc, d10, d30, flags = values
            require(0 <= zone < 32 and address == zone * (256 << 20), "区地址或序号不匹配")
            require(0 <= ch < 8 and 0 <= lun < 4 and 0 <= plane < 2 and block == zone,
                    "物理地址不符合当前实验几何")
            require(block_id == (ch * 4 + lun) * 2 + plane, "稳定块编号不匹配物理地址")
            require(born <= now and 0 <= d10 <= d30 <= rc, "窗口计数或时间矛盾")
            require(flags & 1 and not flags & ~15, "窗口标志不合法")
            require(not flags & 2 or now-born >= 10*SECOND, "十秒窗口尚未预热")
            require(not flags & 4 or (flags & 2 and now-born >= 30*SECOND and not flags & 8),
                    "三十秒窗口完整性标志矛盾")
            records = result.setdefault(stage, {})
            require(zone not in records, "同一阶段出现重复区")
            records[zone] = dict(区地址=address, 会话=[session_hi, session_lo], 代际=generation,
                                 块编号=block_id, PPA=[ch, lun, plane, block], 快照纳秒=now,
                                 代际起点纳秒=born, RC=rc, 十秒增量=d10, 三十秒增量=d30, 标志=flags)
    for stage, zones in result.items():
        require(set(zones) == set(range(32)), f"{stage}不是完整32区报告")
    return result


def analyze(directory):
    require(directory.is_dir(), "原始结果目录不存在")
    for phase in ["query", "cache-flush-check", "windows-after-flush"]:
        require((directory/f"{phase}.exit").read_text().strip() == "0", f"{phase}未成功")
    require((directory/"windows.exit").read_text().strip() == "1", "原始失败记录缺失或被覆盖")
    require((directory/"library-build.exit").read_text().strip() == "2", "原始编译失败记录缺失")
    require((directory/"library-build-retry.exit").read_text().strip() == "0", "库重新编译未成功")
    require((directory/"test-build-after-flush.exit").read_text().strip() == "0", "修正后测试编译未成功")
    observations = read(directory, "windows-after-flush")
    require(list(observations) == STAGES, "窗口测试阶段缺失或顺序不符")
    rows = [observations[s][31] for s in STAGES]
    a, b = rows[1], rows[2]
    expected = [(0,0,0), (20,20,20), (35,35,35), (35,0,35), (42,7,42), (42,0,7),
                (0,0,0), (0,0,0), (0,0,0)]
    for stage, row, counts in zip(STAGES, rows, expected):
        require((row["RC"],row["十秒增量"],row["三十秒增量"]) == counts, f"{stage}定量计数不符")
        require(row["会话"] == rows[0]["会话"], "设备会话中途变化")
    require(a["块编号"] != b["块编号"], "最大块没有实际切换")
    require(all(r["块编号"] == b["块编号"] for r in rows[2:6]), "块B身份意外变化")
    require(all(r["代际"] == a["代际"] for r in rows[:6]), "非Reset阶段代际变化")
    require(all(r["代际"] == a["代际"]+1 for r in rows[6:]), "Reset代际没有只递增一次")
    require([r["标志"] for r in rows[6:]] == [1,3,7], "Reset后窗口预热顺序不符")
    require(all(rows[i+1]["快照纳秒"] > rows[i]["快照纳秒"] for i in range(len(rows)-1)),
            "阶段时间不递增")
    for zones in observations.values():
        require(all(zones[i]["RC"] == 0 and zones[i]["代际"] == 0 for i in range(31)),
                "未参与测试的其他区计数或代际变化")
    final_zones = json.loads((directory/"windows-after-flush.zones.after.json").read_text())["zone_list"]
    require(len(final_zones) == 32 and all(z["state"] == "EMPTY" and z["wp"] == z["slba"] for z in final_zones),
            "终态不是32区全空")
    return {"核验通过":True, "来源":str(directory), "真实性":"隔离客机真实NVMe及ZenFS后端查询",
            "目标区":31, "目标区阶段观测":dict(zip(STAGES,rows)), "其他31区未扰动":True,
            "终态32区全空":True, "初次失败已保留":True, "RR与ZC执行测试":False,
            "统计开销或机制收益已验证":False}


if __name__ == "__main__":
    require(len(sys.argv) == 2, "需要一个原始结果目录")
    print(json.dumps(analyze(Path(sys.argv[1])), ensure_ascii=False, indent=2))
