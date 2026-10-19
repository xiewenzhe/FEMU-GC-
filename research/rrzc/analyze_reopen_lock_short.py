#!/usr/bin/env python3
"""核验修复后固定300秒功能运行；不是收益对照，不放行旧失败结果。"""
import hashlib
import json
from pathlib import Path
import re
import sys

from analyze_stream import rows, check_trace
from analyze_v1_shadow_stream import require
from guarded_rounds import parse
from analyze_tail_stream import ledger
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from analyze_v1_trajectory import read_samples
from analyze_guarded_stream import window_account


def overlaps(lines):
    """只报告日志上迁移尚未结束时重开；不能代替线程栈或强制交错。"""
    active = {}; events = []
    for n, line in enumerate(lines, 1):
        if (m := re.search(r"MigrateFileExtents, fname: (.*), extent count:", line)):
            active[m[1]] = n
        if (m := re.search(r"MigrateFileExtents Finished, fname: (.*), extent count:", line)):
            active.pop(m[1], None)
        if (m := re.search(r"Reopen writable file: (\S+)", line)) and m[1] in active:
            events.append(dict(文件=m[1],迁移开始行=active[m[1]],重开开始行=n))
    return events


def analyze(run):
    require(run.name == "reopen-lock-short-20261015", "不是本轮固定短测")
    for name in ("mkfs.exit", "run.exit", "verify.exit", "runner.exit"):
        require((run/name).read_text().strip() == "0", name+"未成功")
    require(rows(run/"config.csv") == [{"用途": "重开锁序功能回归", "计划业务秒数": "300", "RR阈值": "4096",
        "GC启动比例": "20", "回收预算字节": "201326592", "额外预算字节": "16777216",
        "紧迫界限秒数": "10", "尾部消融": "bypass"}], "固定功能输入改变")
    guest = Path("/home/femu/rrzc-stream-results")/run.name
    for raw in (run/"logs.sha256").read_text().splitlines():
        digest, name = raw.split(maxsplit=1)
        require(hashlib.sha256((run/Path(name).relative_to(guest)).read_bytes()).hexdigest() == digest, "原始日志哈希不符")
    require((run/"identity.initial.sha256").read_bytes() == (run/"identity.final.sha256").read_bytes() and
        (run.parent/"源码身份.before.sha256").read_bytes() == (run.parent/"源码身份.after.sha256").read_bytes(),
        "运行期间源码身份改变")
    actual = {n: d for d, n in (s.split() for s in (run/"identity.initial.sha256").read_text().splitlines())}
    expected = {n: d for d, n in (s.split() for s in (run.parent/"源码身份.before.sha256").read_text().splitlines())}
    require(all(actual.get(n) == d for n, d in expected.items()), "实际业务源码或库不符")
    require(next(d for n, d in actual.items() if n.endswith("/stream_rrzc_test.cc")) ==
        "277ce66cf22a0a665eec2cf23d504e83058165971df6d5c5c3911b74868d276e", "改变了原业务源")
    lines = (run/"zenfs.run.log").read_text().splitlines(); text = "\n".join(lines)
    require("[ERROR]" not in text and "状态=失败" not in text, "真实维护存在错误")
    require(set(re.findall(r"GC 检查：.*启动水平=(\d+)%", text)) == {"20"}, "GC20%未确认")
    require(text.count("独立尾部约束：已启用，真实执行=1，") == 1, "真实执行入口不符")
    operations = rows(run/"operations.csv")
    trace = check_trace(operations, 300, "original")
    commits = [int(m[1]) for line in lines if (m := re.search(r"滚动更新提交：代号=(\d+)，", line))]
    require(commits == list(range(10)), "10代业务没有完整提交，不能称越过停点")
    require("滚动业务读完成：" in text and "业务完整比对字节=687865856" in
        (run/"verify.stdout.log").read_text() and "填充文件首4KiB抽检=20" in (run/"verify.stdout.log").read_text(),
        "读取或656MiB完整数据校验未完成")
    require(not any(s in (run/"zenfs.verify.log").read_text() for s in
        ("GC 检查：", "RR 开始：", "独立约束决策：", "[ERROR]")), "只读校验发生维护或错误")
    decisions, _, maintenance = parse(lines, "guarded", "bypass")
    zc = [tuple(map(int, m.groups())) for line in lines if (m := ZC_STATS.search(line))]
    rr = [tuple(map(int, m.groups())) for line in lines if (m := RR_STATS.search(line))]
    require([r[0] for r in zc] == list(range(1, len(zc)+1)) and
        [r[0] for r in rr] == list(range(1, len(rr)+1)) and text.count("RR 完成：") == len(rr), "维护序号或完成不符")
    require(len(zc) == len(maintenance) and all(int(m["本轮迁移字节"]) == r[1] for m, r in zip(maintenance, zc)),
        "维护结果与决策未闭合")
    events = ledger(lines)
    require(sum(int(c["有效字节"]) for c in events["实际复制"]) == sum(r[1] for r in zc+rr), "实际复制总账不符")
    samples, batches = read_samples(run/"recent.csv")
    require(samples and int(rows(run/"states.csv")[-1]["经过纳秒"]) >= 300*10**9, "未达到固定结束时点")
    return dict(核验通过=True,固定业务秒数=300,完整提交代数=len(commits),完整校验有效数据MiB=656,
        原请求轨迹SHA256=trace,实际改选次数=sum(d["独立核验改选"] for d in decisions),
        ZC次数=len(zc),RR次数=len(rr),决策=decisions,迁移和重开日志交错=overlaps(lines),
        全程功能账目=window_account(lines, events["实际复制"], operations, 0, 300),
        尾部维护账目=events,终态空间=rows(run/"states.csv")[-1],观察采样批数=len(batches),
        已越过原停点=True,原现场死锁已被线程栈证明=False,
        说明="固定300秒功能回归，完整请求／数据／维护闭合；不能代替同版本900秒配对或推广为性能收益。")


if __name__ == "__main__":
    require(len(sys.argv) == 2, "输入本轮固定短测目录")
    print(json.dumps(analyze(Path(sys.argv[1])), ensure_ascii=False, indent=2))
