#!/usr/bin/env python3
"""同库尾部消融短测：核验固定输入和真实维护总账，不要求收益为正。"""
import hashlib
import json
from pathlib import Path
import re
import sys

from analyze_stream import rows
from analyze_periodic import latency
from analyze_tail_pair import check_reads
from analyze_guarded_tail import check_ongoing
from analyze_tail_stream import ledger
from analyze_v1_shadow_stream import fields, require, SECOND
from analyze_controlled_zc import ZC_STATS
from analyze_maintenance_overlap import RR_STATS
from guarded_rounds import parse

LABEL = "tail-ablation-smoke-20261015"
GUEST_LABEL = "guarded-tail-guarded-20261014"
BUILD = Path(__file__).resolve().parent/"results/tail-ablation-build-20261015/rrzc-tail-ablation-build-20261015"
MIB = 1 << 20


def expected_software():
    expected = {"plugin/zenfs/"+name: digest for digest, name in
                (s.split() for s in (BUILD/"source.after.sha256").read_text().splitlines())}
    expected["librocksdb_debug.a"] = next(d for d, n in
        (s.split() for s in (BUILD/"binaries.sha256").read_text().splitlines()) if n == "librocksdb_debug.a")
    return expected


def hashes(path):
    return {name: digest for digest, name in (s.split() for s in path.read_text().splitlines())}


def analyze(run, mode):
    require(mode in ("keep", "bypass") and run.name == GUEST_LABEL and
            run.parent.name == f"{LABEL}-{mode}", "不是本轮独立消融归档")
    for name in ("mkfs.exit", "run.exit", "verify.exit", "runner.exit"):
        require((run/name).read_text().strip() == "0", name+"未成功")
    require((run/"identity.initial.sha256").read_bytes() == (run/"identity.final.sha256").read_bytes(),
            "运行期间软件身份改变")
    require((run.parent/"完整源码身份.before.sha256").read_bytes() ==
            (run.parent/"完整源码身份.after.sha256").read_bytes(), "消融头或库在业务期间改变")
    expected = expected_software()
    require(hashes(run.parent/"完整源码身份.before.sha256") == expected, "非已核验消融构建")
    identity = hashes(run/"identity.initial.sha256")
    require(all(identity.get(n) == d for n, d in expected.items() if not n.endswith("rrzc_tail_ablation.h")),
            "业务实际源码或库不符")
    software = {Path(n).name if n.startswith("/home/femu/") else n: d for n, d in identity.items()}
    require(software["guarded_tail_test.cc"] == "88e588b2a236a9302a1addd5edc5b54e6020002d30ad09bfc37f464c64906c94",
            "固定输入测试源改变")
    guest = Path("/home/femu/rrzc-tail-pair-results")/GUEST_LABEL
    for line in (run/"logs.sha256").read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        require(hashlib.sha256((run/Path(filename).relative_to(guest)).read_bytes()).hexdigest() == digest,
                "原始文件哈希不符")
    config = rows(run/"config.csv")
    require(config == [{"策略": "guarded", "固定观察秒数": "35", "RR阈值": "4096", "GC启动比例": "20",
        "回收预算字节": "201326592", "种子有效字节": "251658240", "种子真实读取次数": "4096",
        "热区预读次数": "3904", "填充区数": "24", "持续读次数": "1120", "持续读速率": "32",
        "相位等待秒数": "5"}], "固定短测输入改变")
    check_reads(rows(run/"seed.reads.csv"), 4096)
    check_reads(rows(run/"hot.reads.csv"), 3904)
    ongoing = rows(run/"ongoing.reads.csv")
    trace = check_ongoing(ongoing)
    before, first, final = rows(run/"states.csv")
    require([s["阶段"] for s in (before, first, final)] == ["释放后起点", "首次迁移后", "固定窗口结束"] and
            before["经过纳秒"] == "0" and 35*SECOND <= int(final["经过纳秒"]) < 36*SECOND,
            "固定观察窗口或阶段改变")
    require(before["预留Zone"] == before["种子Zone"] and int(before["预留尾部"]) == 16*MIB and
            int(before["预留有效量"]) == 240*MIB, "未以真实RR形成预注册尾部")
    require(all(int(s["有效字节"]) == 6432*MIB for s in (before, first, final)), "业务有效量改变")
    recent = rows(run/"recent.csv")
    staged = {(r["阶段"], r["组号"]): r for r in recent if r["阶段"] != "持续观察"}
    require(len(staged) == 6 and sum(r["阶段"] == "持续观察" for r in recent) == 140, "同块轨迹不完整")
    for i in (0, 1):
        a, b, c = (staged[(p, str(i))] for p in ("热读前", "释放前", "观察结束"))
        require(all(a[k] == b[k] == c[k] for k in ("设备会话高位", "设备会话低位", "Zone")), "跨会话或源区")
        require(a["RC"] == "0" and a["设备代际"] == b["设备代际"] and a["块编号"] == b["块编号"],
                "预读跨块或Reset")
        require(b["窗口标志"] == "7" and all(int(b[k]) == (3904 if i else 0) for k in
                ("RC", "十秒真实增量", "三十秒真实增量")), "预读真实增量不符")
        require(int(c["设备代际"]) == int(b["设备代际"])+1 and c["RC"] == "0", "源Reset或清零未确认")
    lines = (run/"zenfs.run.log").read_text().splitlines()
    text = "\n".join(lines)
    require("[ERROR]" not in text and "状态=失败" not in text and
            set(re.findall(r"GC 检查：.*启动水平=(\d+)%", text)) == {"20"}, "维护失败或GC条件改变")
    require(text.count("独立尾部约束：已启用，真实执行=1，") == 1 and
            "独立尾部结束：固定35秒与1120次持续读完成" in text, "执行入口或固定输入未完成")
    require(not any(s in text for s in ("V1真实：已启用", "V1影子：已启用", "短期影子：已启用",
        "成本观察：已启用", "ZC实验单次选区：已启用")), "混用旧入口")
    decisions, files, maintenance = parse(lines, "guarded", mode)
    suggested = [d for d in decisions if d["原会改选"] == "1"]
    require(len(suggested) == 1, "没有形成唯一短期建议，保留现场不追加载荷")
    d = suggested[0]
    require(d["GreedyZone"] == before["冷Zone"] and d["短期建议Zone"] == before["热Zone"] and
            d["实际改选"] == str(int(mode == "bypass")), "未观察到预注册的保留／旁路路径")
    require(d["成本核验"].get("原尾部核验", d["成本核验"])["分类"] == "未知回退", "原尾部判断与功能输入不符")
    moved, stayed = ("热Zone", "冷Zone") if mode == "bypass" else ("冷Zone", "热Zone")
    require(first[moved] != before[moved] and first[stayed] == before[stayed], "首次维护映射不符")
    zc = [tuple(map(int, m.groups())) for line in lines if (m := ZC_STATS.search(line))]
    rr = [tuple(map(int, m.groups())) for line in lines if (m := RR_STATS.search(line))]
    require(rr and rr[0] == (1, 240*MIB, 1, 240*MIB), "共同准备RR不符")
    require(len(zc) == len(maintenance) and text.count("RR 完成：") == len(rr), "维护完成数量未闭合")
    total = sum(r[1] for r in zc+rr)
    results = ledger(lines)
    require(sum(int(c["有效字节"]) for c in results["实际复制"]) == total, "复制总账与维护统计不符")
    require(rows(run/"tail.verify.csv") == [{"完整有效数据字节": str(288*MIB), "填充文件数": "24",
        "每填充文件抽检首尾字节": "4096"}], "数据校验未完成")
    require(not any(s in (run/"zenfs.verify.log").read_text() for s in
        ("GC 检查：", "RR 开始：", "独立约束决策：", "[ERROR]")), "只读校验引入维护或错误")
    return dict(核验通过=True,模式=mode,来源=str(run),配置=config[0],软件身份=software,
        完整核心身份=expected,持续读计划哈希=trace,首次选区前布局=before,首次维护后布局=first,终态布局=final,
        决策=decisions,候选文件=files,共同准备RR次数=1,后续RR次数=len(rr)-1,ZC次数=len(zc),
        全程迁移MiB=total/MIB,重复文件复制MiB=results["重复文件复制字节"]/MIB,
        提前替换次数=len(results["预留替换"]),尾部事件账目=results,
        持续读API耗时=latency([int(r["API耗时纳秒"]) for r in ongoing]),
        说明="同库固定功能消融；不要求收益为正，不代表滚动稳态或论文性能结论。")


def compare(keep, bypass):
    require(keep["模式"] == "keep" and bypass["模式"] == "bypass", "配对顺序不符")
    for key in ("配置", "软件身份", "完整核心身份", "持续读计划哈希", "首次选区前布局"):
        require(keep[key] == bypass[key], "配对输入或软件不同："+key)
    return dict(核验通过=True,保留尾部准入=keep,仅旁路尾部准入=bypass,
        旁路净迁移增加MiB=bypass["全程迁移MiB"]-keep["全程迁移MiB"],
        旁路净重复复制增加MiB=bypass["重复文件复制MiB"]-keep["重复文件复制MiB"],
        说明="相同35秒业务输入；完整性能／净收益结论还需固定滚动配对和重复实验。")


if __name__ == "__main__":
    require(len(sys.argv) == 3, "输入keep／bypass两个归档目录")
    print(json.dumps(compare(analyze(Path(sys.argv[1]), "keep"), analyze(Path(sys.argv[2]), "bypass")),
                     ensure_ascii=False, indent=2))
