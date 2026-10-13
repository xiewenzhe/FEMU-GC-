"""合成采样只用于核验器单元测试，不是实验结果。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_controlled_future import validate_counts, validate_samples, validate_equal_runs


def sample():
    return {"窗口毫秒": 100, "Zone": 256, "主机代际前": 0, "主机代际后": 0,
            "观察序号前": 0, "观察序号后": 0, "重置窗稳定": 1, "最大读数": 4096,
            "读数总和": 4096, "80%风险块": 1, "90%风险块": 1, "达阈值块": 1, "首次采样跨阈值": 1}


class FutureAnalysisTest(unittest.TestCase):
    @staticmethod
    def equal_runs():
        return [{"速率条件": level, "实际每文件业务读次数": dict.fromkeys(range(4), 768),
                 "软件身份": ["合成身份"], "初始四格": ["合成初始状态"]}
                for level in ("equal-slow", "equal-fast")]

    def test_equal_actual_counts_pass(self):
        validate_equal_runs(self.equal_runs())

    def test_equal_plan_but_actual_shortfall_is_rejected(self):
        runs = self.equal_runs()
        runs[1]["实际每文件业务读次数"][0] = 767
        with self.assertRaises(ValueError):
            validate_equal_runs(runs)

    def test_equal_comparison_missing_or_duplicate_condition_is_rejected(self):
        for levels in (("equal-slow",), ("equal-slow", "equal-slow")):
            runs = self.equal_runs()[:len(levels)]
            for run, level in zip(runs, levels):
                run["速率条件"] = level
            with self.assertRaises(ValueError):
                validate_equal_runs(runs)

    def test_equal_identity_or_initial_state_change_is_rejected(self):
        for key in ("软件身份", "初始四格"):
            runs = self.equal_runs()
            runs[1][key] = ["另一个合成状态"]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_equal_runs(runs)

    def test_first_cross_is_same_generation(self):
        self.assertEqual(len(validate_samples([sample()])), 1)

    def test_duplicate_same_generation_cross_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_samples([sample(), sample()])

    def test_new_generation_can_cross_again(self):
        a, b = sample(), sample()
        b.update({"窗口毫秒": 200, "主机代际前": 1, "主机代际后": 1, "观察序号前": 2, "观察序号后": 2})
        self.assertEqual(len(validate_samples([a, b])), 2)

    def test_unstable_or_below_threshold_cross_is_rejected(self):
        for key, value in (("重置窗稳定", 0), ("最大读数", 4095), ("达阈值块", 0), ("主机代际后", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                row = sample(); row[key] = value
                validate_samples([row])

    def test_real_counts_may_be_below_plan(self):
        rows = [{"窗口毫秒": 40000, "组号": i, "累计业务4KiB读次数": 180} for i in range(4)]
        self.assertEqual(validate_counts(rows, 8, 24, 40), dict.fromkeys(range(4), 180))

    def test_wrong_counts_or_short_window_is_rejected(self):
        for key, value in (("窗口毫秒", 39999), ("累计业务4KiB读次数", 193)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                rows = [{"窗口毫秒": 40000, "组号": i, "累计业务4KiB读次数": 192} for i in range(4)]
                rows[0][key] = value
                validate_counts(rows, 8, 24, 40)

    def test_missing_group_or_empty_trace_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_counts([], 8, 24, 40)
        with self.assertRaises(ValueError):
            validate_samples([])


if __name__ == "__main__":
    unittest.main()
