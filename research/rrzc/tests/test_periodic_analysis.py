"""合成输入仅用于统计核验器单元测试，不是实验结果。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_periodic import MIB, percentile, latency, periodic_stability, validate_pair, validate_decisions


class PeriodicAnalysisTest(unittest.TestCase):
    def test_nearest_rank(self):
        self.assertEqual(percentile([1, 2, 3, 4], .5), 2)
        self.assertEqual(percentile([1, 2, 3, 4], .99), 4)

    def test_empty_latency(self):
        self.assertIsNone(percentile([], .99))
        self.assertIsNone(latency([])["p99微秒"])

    def test_latency_unit(self):
        self.assertEqual(latency([1000, 3000])["平均微秒"], 2)

    @staticmethod
    def stable():
        return ([{"ZC次数": 1, "RR完成次数": 1, "总维护迁移字节": 96*MIB} for _ in range(4)],
                [{"有效字节": 6752*MIB, "空闲字节": 672*MIB, "满区可回收字节": 0} for _ in range(4)])

    def test_periodic_screen(self):
        windows, ends = self.stable()
        self.assertTrue(all(periodic_stability(windows, ends)["筛查"].values()))

    def test_short_not_stable(self):
        windows, ends = self.stable()
        self.assertFalse(periodic_stability(windows[:3], ends[:3])["筛查"]["至少四个预热后完整周期"])

    def test_space_drift(self):
        windows, ends = self.stable()
        ends[-1]["有效字节"] += 64*MIB
        self.assertFalse(periodic_stability(windows, ends)["筛查"]["边界有效量波动不超过32MiB"])

    def test_maintenance_drift(self):
        windows, ends = self.stable()
        windows[-1]["RR完成次数"] = 4
        self.assertFalse(periodic_stability(windows, ends)["筛查"]["每周期RR完成次数变异系数不超过0.25"])

    def test_no_events_not_stable(self):
        windows, ends = self.stable()
        for w in windows:
            w.update(ZC次数=0, RR完成次数=0, 总维护迁移字节=0)
        self.assertFalse(periodic_stability(windows, ends)["筛查"]["存在重复的有搬运维护事件"])

    def test_pair_rejects_trace_difference(self):
        runs = [{"配置": {"策略": s}, "软件身份": "合成相同", "实际轨迹sha256": s} for s in ("valid", "weighted")]
        with self.assertRaises(ValueError): validate_pair(runs)

    @staticmethod
    def decision(mode, zone):
        return [
            "简单双因素候选：Zone=1，有效字节=32，容量字节=256，最大块读数=448，读数阈值=4096，覆盖资格=1，空目标资格=1，读压力可用=1，空间分量ppm=875000，风险分量ppm=109375，等权分数ppm=492187",
            "简单双因素候选：Zone=2，有效字节=64，容量字节=256，最大块读数=3584，读数阈值=4096，覆盖资格=1，空目标资格=1，读压力可用=1，空间分量ppm=750000，风险分量ppm=875000，等权分数ppm=812500",
            f"简单双因素实际集合：模式={mode}，原合格区=2，实际选中=1，Zone={zone}，回收预算=192，实际使用等权={int(mode=='weighted')}，等权分数ppm={492187 if zone==1 else 812500}"]

    def test_recompute_choices(self):
        for mode, zone in (("valid", 1), ("weighted", 2)):
            result = validate_decisions(self.decision(mode, zone), mode, 192)
            self.assertEqual(result["成功单区决策"][0]["实际选择"]["Zone"], zone)

    def test_reject_wrong_choice(self):
        with self.assertRaises(ValueError): validate_decisions(self.decision("weighted", 1), "weighted", 192)

    def test_reject_wrong_score(self):
        lines = self.decision("weighted", 2)
        lines[0] = lines[0].replace("空间分量ppm=875000", "空间分量ppm=874999")
        with self.assertRaises(ValueError): validate_decisions(lines, "weighted", 192)

    def test_zero_candidates_not_actual_fallback(self):
        result = validate_decisions(["简单双因素实际集合：模式=valid，原合格区=0，实际选中=0，状态=单区预算不可满足，退回原批量策略"], "valid", 192)
        self.assertEqual(result["非空实际批量回退次数"], 0)
        self.assertEqual(result["零候选空集合轮数"], 1)

    def test_recompute_8020(self):
        lines = [
            "简单双因素候选：Zone=1，有效字节=16，容量字节=256，最大块读数=0，读数阈值=4096，覆盖资格=1，空目标资格=1，读压力可用=1，空间分量ppm=937500，风险分量ppm=0，加权分数ppm=750000",
            "简单双因素候选：Zone=2，有效字节=64，容量字节=256，最大块读数=2048，读数阈值=4096，覆盖资格=1，空目标资格=1，读压力可用=1，空间分量ppm=750000，风险分量ppm=500000，加权分数ppm=700000",
            "简单双因素实际集合：模式=weighted，原合格区=2，实际选中=1，Zone=1，回收预算=192，实际使用加权=1，加权分数ppm=750000"]
        result = validate_decisions(lines, "weighted", 192, 80)
        self.assertEqual(result["成功单区决策"][0]["实际选择"]["Zone"], 1)
        with self.assertRaises(ValueError):
            validate_decisions(lines, "weighted", 192)
        lines[0] = lines[0].replace("加权分数ppm=750000", "加权分数ppm=750001")
        with self.assertRaises(ValueError):
            validate_decisions(lines, "weighted", 192, 80)

    def test_invalid_weight(self):
        with self.assertRaises(ValueError):
            validate_decisions([], "weighted", 192, 101)


if __name__ == "__main__": unittest.main()
