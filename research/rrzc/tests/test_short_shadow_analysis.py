"""合成输入核验短期影子排序、同块差分及观察边界；不是实际SSD收益结果。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_short_shadow_stream import short_eta, choose_short, future_view, rounds, MAX, SECOND, stamp
from analyze_v1_trajectory import point_from_candidate


class ShortShadowAnalysis(unittest.TestCase):
    def setUp(self):
        self.block = {"统计准入":"1", "设备会话":"1/2", "PPA":"0/0/0/0", "设备代际":"0",
            "块编号":"0", "代际起点纳秒":"0", "Zone":"0", "窗口标志":"7",
            "快照纳秒":str(30*SECOND), "当前RC":"3900"}
        self.growth = {"增量可知":"1", "增长准入":"1", "有前点":"1", "前会话":"1/2",
            "前PPA":"0/0/0/0", "前代际":"0", "前块编号":"0", "前代际起点纳秒":"0",
            "前区编号":"0", "前标志":"7", "前快照纳秒":str(29*SECOND), "前RC":"3868",
            "间隔纳秒":str(SECOND), "真实增量":"32", "短期达阈值纳秒":"6125000000"}
        self.candidates = [self.candidate(1, 16), self.candidate(2, 32)]

    @staticmethod
    def candidate(zone, valid):
        return {"Zone":str(zone), "有效字节":str(valid<<20), "容量字节":str(256<<20),
            "覆盖资格":"1", "空目标资格":"1"}

    def test_same_block_rate(self):
        self.assertEqual(short_eta(self.block, self.growth), 6125000000)

    def test_short_does_not_require_long_warmup(self):
        self.block["窗口标志"] = "1"
        self.assertEqual(short_eta(self.block, self.growth), 6125000000)

    def test_stopped_block(self):
        self.growth.update(增长准入="0", 前RC="3900", 真实增量="0", 短期达阈值纳秒=str(MAX))
        self.assertEqual(short_eta(self.block, self.growth), MAX)

    def test_reached_threshold_with_zero_delta(self):
        self.block["当前RC"] = "4096"
        self.growth.update(增长准入="0", 前RC="4096", 真实增量="0", 短期达阈值纳秒=str(MAX))
        self.assertEqual(short_eta(self.block, self.growth), 0)

    def test_unknown_growth(self):
        self.growth.update(增量可知="0", 增长准入="0")
        self.assertIsNone(short_eta(self.block, self.growth))

    def test_unknown_cannot_claim_pass(self):
        self.growth["增量可知"] = "0"
        with self.assertRaises(ValueError): short_eta(self.block, self.growth)

    def test_identity_changes_rejected(self):
        for key in ("前会话", "前代际", "前块编号", "前PPA", "前代际起点纳秒", "前区编号"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                short_eta(self.block, dict(self.growth, **{key:"99"}))

    def test_wrong_delta_rejected(self):
        self.growth["真实增量"] = "31"
        with self.assertRaises(ValueError): short_eta(self.block, self.growth)

    def test_switch(self):
        self.assertEqual(choose_short(self.candidates, {1:MAX, 2:6*SECOND})[:5],
                         (1, 2, True, 16<<20, 6*SECOND))

    def test_greedy_unknown(self):
        self.assertFalse(choose_short(self.candidates, {2:6*SECOND})[2])

    def test_greedy_urgent(self):
        self.assertEqual(choose_short(self.candidates, {1:9*SECOND, 2:6*SECOND})[:3], (1, 1, False))

    def test_slow_or_stopped_alternative(self):
        for estimate in (None, MAX, 11*SECOND):
            with self.subTest(estimate=estimate):
                self.assertFalse(choose_short(self.candidates, {1:MAX, 2:estimate})[2])

    def test_extra_budget(self):
        self.candidates[1] = self.candidate(2, 33)
        self.assertIn("超出", choose_short(self.candidates, {1:MAX, 2:SECOND})[-1])

    def test_original_qualification(self):
        for key in ("覆盖资格", "空目标资格"):
            self.candidates[1] = dict(self.candidate(2, 32), **{key:"0"})
            self.assertFalse(choose_short(self.candidates, {1:MAX, 2:SECOND})[2])

    def test_ties_by_valid_then_zone(self):
        self.candidates.append(self.candidate(3, 24))
        self.assertEqual(choose_short(self.candidates, {1:MAX, 2:SECOND, 3:SECOND})[1], 3)
        self.candidates[2] = self.candidate(3, 32)
        self.assertEqual(choose_short(self.candidates, {1:MAX, 2:SECOND, 3:SECOND})[1], 2)

    def test_no_candidates(self):
        self.assertEqual(choose_short([], {})[:3], (None, None, False))

    @staticmethod
    def empty_round():
        # 合成无候选轮次，用于检查日志闭合和安全字段，不代表设备日志。
        common = {"轮次":"1", "存在Greedy":"0", "实际Greedy":"0", "会改选":"0",
            "额外有效搬运字节":"0", "预测达阈值纳秒":str(MAX), "界限纳秒":str(10*SECOND),
            "预算字节":str(16<<20), "帧可用":"1", "原因":"没有合格候选", "实际改选":"0"}
        old = dict(common, 影子建议="0")
        short = dict(common, 建议Zone="0")
        growth = {"轮次":"1", "原建议Zone":"0", "原会改选":"0", "原预测达阈值纳秒":str(MAX),
            "准入后建议Zone":"0", "准入后会改选":"0"}
        def line(marker, values):
            return marker+"，".join(k+"="+v for k,v in values.items())
        return [line("V1增长决策：",growth), line("短期影子决策：",short),
                line("V1影子决策：",old), "简单双因素实际集合：实际选中=0"]

    def test_empty_round_closed(self):
        decisions, observations, files = rounds(self.empty_round())
        self.assertEqual((len(decisions), observations, files), (1, [], []))

    def test_missing_shadow_round_rejected(self):
        lines = self.empty_round(); del lines[1]
        with self.assertRaises(ValueError): rounds(lines)

    def test_wrong_logged_budget_rejected(self):
        lines = self.empty_round(); lines[1] = lines[1].replace("预算字节=16777216", "预算字节=1")
        with self.assertRaises(ValueError): rounds(lines)

    def test_invalid_boolean_rejected(self):
        lines = self.empty_round(); lines[1] = lines[1].replace("会改选=0", "会改选=2")
        with self.assertRaises(ValueError): rounds(lines)

    def test_claimed_actual_switch_rejected(self):
        lines = self.empty_round(); lines[1] = lines[1].replace("实际改选=0", "实际改选=1")
        with self.assertRaises(ValueError): rounds(lines)

    def future(self, records, later=(), files=None):
        decision = "2026/10/14-00:00:30.000000 1 决策"
        lines = [decision]+list(later)
        point = point_from_candidate(self.block)
        samples = [dict(point, time=t*SECOND, rc=rc, generation=gen) for t, rc, gen in records]
        host = {(0, r["time"]):r["time"] for r in samples}
        observation = dict(Zone=0, 决策行号=1, 轮次="1", 当前块=self.block,
            短期预测纳秒=6*SECOND, 有效字节=32<<20)
        if files is None:
            files = [{"轮次":"1", "Zone":"0", "文件编号":"8", "区内有效字节":str(32<<20)}]
        return future_view(lines, {0:samples}, host, stamp(decision)-30, observation, files)

    def test_cross_is_separate_from_rr_start(self):
        result = self.future([(35, 4090, 0), (36, 4100, 0)],
            ["2026/10/14-00:00:40.000000 1 RR 开始：Zone 0，达阈值物理块 1，有效数据 33554432 字节，封闭剩余容量 0 字节"])
        self.assertEqual(result["短期预测判定"], "命中")
        self.assertEqual(result["阈值跨越观测"]["上界秒"], 6)
        self.assertEqual(result["后续首次事件"]["距离决策秒"], 10)

    def test_stopped_for_ten_seconds_is_false_positive(self):
        result = self.future([(31, 3916, 0), (41, 3916, 0)])
        self.assertEqual(result["短期预测判定"], "误报")

    def test_early_retirement_is_censored_not_ten_second_false_positive(self):
        result = self.future([(31, 3916, 0), (32, 5000, 1)],
            ["2026/10/14-00:00:31.500000 1 V1后续文件退役：文件编号=8，文件名=测试",
             "2026/10/14-00:00:31.600000 1 Zone 重置诊断：Zone=0，状态=成功，代际=1"])
        self.assertIsNone(result["十秒内跨阈值可确认"])
        self.assertEqual(result["后续首次事件"]["事件"], "候选文件最后链接全部删除")
        self.assertEqual(result["维护或退役前最大已观测RC"], 3916)

    def test_maintenance_reads_excluded(self):
        result = self.future([(31, 3916, 0), (33, 5000, 0)],
            ["2026/10/14-00:00:32.000000 1 简单双因素实际集合：Zone=0，实际选中=1"])
        self.assertIsNone(result["十秒内跨阈值可确认"])
        self.assertEqual(result["维护或退役前最大已观测RC"], 3916)

    def test_reset_generation_is_unknown(self):
        result = self.future([(31, 3916, 0), (41, 5000, 1)])
        self.assertIsNone(result["十秒内跨阈值可确认"])
        self.assertEqual(result["阈值跨越观测"]["原因"], "Reset代际变化")

    def test_cross_interval_straddles_horizon_is_unknown(self):
        result = self.future([(39, 4090, 0), (41, 4100, 0)])
        self.assertIsNone(result["十秒内跨阈值可确认"])

    def test_incomplete_file_coverage_rejected(self):
        with self.assertRaises(ValueError):
            self.future([], files=[{"轮次":"1", "Zone":"0", "文件编号":"8", "区内有效字节":"1"}])


if __name__ == "__main__": unittest.main()
