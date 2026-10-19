"""合成消融日志回归，不代表真实设备收益。"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_guarded_analysis as base
from guarded_rounds import parse
from tail_ablation_log import TailAblationLog
from analyze_v1_shadow_stream import MAX, SECOND, eta


def startup(mode):
    return ("尾部消融：已启用，护栏启用=" + str(int(mode == "keep")) +
            "，仅修改尾部准入，短期建议预算和稳定性复核不变")


def diagnostic(mode, would=True):
    bypass = mode == "bypass" and would
    return {"轮次": "1", "护栏启用": str(int(mode == "keep")),
            "原会改选": str(int(would)), "原尾部允许": "0",
            "原尾部分类": "未知回退" if would else "无需约束",
            "生效尾部允许": str(int(bypass)),
            "生效尾部分类": "消融旁路" if bypass else "未知回退" if would else "无需约束"}


def fixture(mode, stable=True, sources=(True, True)):
    """构造两个真实资格及同块预测均完整的合成候选。"""
    make = base.GuardedAnalysis()
    out = [startup(mode)]
    zones = [3*(256 << 20), 4*(256 << 20)]
    for i, z in enumerate(zones):
        valid = (32 if i else 16) << 20
        rc = 4000 if i else 0
        space = ((256 << 20)-valid)*1000000//(256 << 20)
        risk = rc*1000000//4096
        c = {"Zone": z, "有效字节": valid, "容量字节": 256 << 20,
             "覆盖资格": 1, "空目标资格": 1, "读压力可用": 1,
             "最大块读数": rc, "读数阈值": 4096, "空间分量ppm": space,
             "风险分量ppm": risk, "等权分数ppm": (space+risk)//2}
        b = {"轮次": "1", "Zone": str(z), "统计准入": "1", "当前RC": str(rc),
             "十秒真实增量": str(rc), "三十秒真实增量": str(rc),
             "快照纳秒": str(30*SECOND), "代际起点纳秒": "0", "窗口标志": "7",
             "PPA": f"0/0/0/{i+3}", "块编号": "0", "原候选资格": "1",
             "设备会话": "1/2", "设备代际": "0"}
        b.update(预测可知="1", 预测达阈值纳秒=str(eta(b)))
        predicted = 3*SECOND if i else MAX
        g = {"轮次": "1", "Zone": str(z), "增量可知": "1", "增长准入": str(i),
             "有前点": "1", "前会话": "1/2", "前PPA": b["PPA"], "前代际": "0",
             "前块编号": "0", "前代际起点纳秒": "0", "前区编号": str(i+3),
             "前标志": "7", "前快照纳秒": str(29*SECOND),
             "前RC": str(rc-32 if i else 0), "间隔纳秒": str(SECOND),
             "真实增量": "32" if i else "0", "短期达阈值纳秒": str(predicted)}
        p = {"轮次": 1, "Zone": z, "次序": 1, "容量字节": 256 << 20,
             "逻辑字节": valid, "对齐字节": 512}
        s = {"轮次": 1, "Zone": z, "有效字节": valid, "短期预测可知": 1,
             "短期达阈值纳秒": predicted, "物理界限可知": 1, "覆盖字节": valid,
             "片段数": 1, "首下界字节": valid, "首上界字节": valid+512,
             "总下界字节": valid, "总上界字节": valid+512}
        out += [base.line(m, r) for m, r in (("简单双因素候选：", c), ("V1影子候选：", b),
            ("V1增长候选：", g), ("独立约束片段：", p), ("独立约束候选：", s))]
    old = {"轮次": 1, "原建议Zone": zones[1], "原会改选": 1,
           "原预测达阈值纳秒": 720000000, "准入后建议Zone": zones[1], "准入后会改选": 1}
    out += [base.line("V1增长决策：", old), base.line("尾部消融核验：", diagnostic(mode))]
    pool = make.pool()
    pool.update(剩余字节=str(16 << 20), 有效字节=str(240 << 20))
    for k in ("剩余字节", "有效字节"):
        pool["复核"+k] = pool[k]
    if not stable:
        pool.update(复核稳定="0", 复核主机代际="1")
    effective = mode == "bypass"
    if effective and stable:
        for z, passed in zip(zones, sources):
            r = make.source()
            r.update(轮次="1", Zone=str(z), 当前写指针=str(z+(256 << 20)),
                     原写指针=str(z+(256 << 20)), 当前有效字节=str((16 if z == zones[0] else 32) << 20))
            r["原有效字节"] = r["当前有效字节"]
            if not passed:
                r.update(通过="0", 稳定可信="1/1/0")
            out.append(base.line("独立约束源复核：", r))
            if not passed:
                break
    checked = effective and stable and sources == (True, True)
    actual = zones[1] if checked else zones[0]
    d = {"轮次": 1, "真实执行": 1, "存在Greedy": 1, "GreedyZone": zones[0],
         "短期建议Zone": zones[1], "原会改选": 1, "原额外有效字节": 16 << 20,
         "预测达阈值纳秒": 3*SECOND, "界限纳秒": 10*SECOND, "预算字节": 16 << 20,
         "尾部允许": int(effective), "尾部分类": "消融旁路" if effective else "未知回退",
         "复核后可改选": int(checked), "实际改选": int(checked), "实际Zone": actual,
         "实际额外有效字节": (16 << 20) if checked else 0}
    out += [base.line("独立约束预留：", pool), base.line("独立约束决策：", d),
        base.line("简单双因素实际集合：", {"实际选中": 1, "Zone": actual, "实际使用等权": 0}),
        base.line("独立约束维护结果：", {"轮次": 1, "实际Zone": actual, "实际改选": int(checked),
            "选择时额外有效字节": d["实际额外有效字节"], "迁移成功": 1, "本轮重置区数": 1})]
    return out


class TailAblationAnalysis(unittest.TestCase):
    def test_keep_reconstructs_original_rejection(self):
        self.assertFalse(parse(fixture("keep"), "guarded", "keep")[0][0]["独立核验改选"])

    def test_bypass_switches_only_after_both_source_checks(self):
        d = parse(fixture("bypass"), "guarded", "bypass")[0][0]
        self.assertTrue(d["独立核验改选"])
        self.assertEqual(d["成本核验"]["原尾部核验"]["分类"], "未知回退")

    def test_bypass_does_not_override_unstable_reserve(self):
        self.assertFalse(parse(fixture("bypass", stable=False), "guarded", "bypass")[0][0]["独立核验改选"])

    def test_bypass_does_not_override_failed_source(self):
        for result in ((False, True), (True, False)):
            with self.subTest(result=result):
                self.assertFalse(parse(fixture("bypass", sources=result), "guarded", "bypass")[0][0]["独立核验改选"])

    def test_default_refuses_treatment_logs(self):
        with self.assertRaises(ValueError):
            parse(fixture("bypass"), "guarded")

    def test_wrong_treatment_refused(self):
        with self.assertRaises(ValueError):
            parse(fixture("bypass"), "guarded", "keep")

    def test_missing_source_cannot_claim_switch(self):
        lines = [s for s in fixture("bypass") if "独立约束源复核：" not in s]
        with self.assertRaises(ValueError):
            parse(lines, "guarded", "bypass")

    def test_false_original_cost_refused(self):
        lines = [s.replace("原尾部允许=0", "原尾部允许=1") for s in fixture("bypass")]
        with self.assertRaises(ValueError):
            parse(lines, "guarded", "bypass")

    def test_missing_round_diagnostic_refused(self):
        with self.assertRaises(ValueError):
            parse([s for s in fixture("keep") if "尾部消融核验：" not in s], "guarded", "keep")

    def test_duplicate_round_diagnostic_refused(self):
        lines = fixture("bypass")
        i = next(i for i, s in enumerate(lines) if "尾部消融核验：" in s)
        lines.insert(i, lines[i])
        with self.assertRaises(ValueError):
            parse(lines, "guarded", "bypass")

    def test_no_proposal_cannot_be_invented_by_bypass(self):
        make = base.GuardedAnalysis()
        lines = make.empty_round()
        lines[2] = lines[2].replace("真实执行=0", "真实执行=1")
        lines.insert(2, base.line("尾部消融核验：", diagnostic("bypass", False)))
        self.assertFalse(parse([startup("bypass")]+lines, "guarded", "bypass")[0][0]["独立核验改选"])

    def test_invalid_and_shadow_modes_refused(self):
        for mode, strategy in (("unknown", "guarded"), ("bypass", "greedy")):
            with self.subTest(mode=mode, strategy=strategy), self.assertRaises(ValueError):
                TailAblationLog(mode, strategy)

    def test_startup_must_precede_diagnostic(self):
        log = TailAblationLog("bypass", "guarded")
        with self.assertRaises(ValueError):
            log.observe(base.line("尾部消融核验：", diagnostic("bypass")))

    def test_changed_budget_still_refused(self):
        with self.assertRaises(ValueError):
            parse([s.replace("预算字节=16777216", "预算字节=16777217") for s in fixture("bypass")],
                  "guarded", "bypass")


if __name__ == "__main__":
    unittest.main()
