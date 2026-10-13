"""合成成本只用于静态预算器单元测试，不是实验数据或策略收益。"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_candidate_costs import ZONE_BYTES, enumerate_batches, write_reports


def fixture(rr_mib):
    return [{"组号": i, "当前有效字节": (32 if i < 2 else 128) << 20,
             "当前无效字节": (224 if i < 2 else 128) << 20, "原门槛选中": i < 2,
             "基线路径窗口内同代RR字节": rr_mib << 20 if i == 2 else 0} for i in range(4)]


def plan(result, indices):
    return next(p for p in result["假设组合"] if p["组合组号"] == indices)


class CandidateCostsTest(unittest.TestCase):
    def test_all_nonempty_subsets_and_baseline(self):
        result = enumerate_batches(fixture(64))
        self.assertEqual(len(result["假设组合"]), 15)
        self.assertEqual(result["基线组号"], [0, 1])
        self.assertEqual(result["基线当前迁移预算字节"], 64 << 20)
        self.assertEqual(result["同等空间参考（基线当前无效字节）"], 448 << 20)

    def test_two_resets_do_not_mean_equal_net_space(self):
        p = plan(enumerate_batches(fixture(64)), [0, 2])
        self.assertEqual(p["源Reset总容量预算字节"], 512 << 20)
        self.assertEqual(p["当前无效空间预算字节"], 352 << 20)
        self.assertFalse(p["满足基线无效空间预算"])
        self.assertIsNone(p["静态差额字节（非实测收益）"])

    def test_unchanged_gate_has_only_original_budget_satisfying_set(self):
        result = enumerate_batches(fixture(128))
        sets = [p["组合组号"] for p in result["假设组合"]
                if p["保持原门槛可入选"] and p["满足基线无效空间预算"]]
        self.assertEqual(sets, [[0, 1]])

    def test_extra_hot_zone_cost_is_not_automatically_saved(self):
        before = plan(enumerate_batches(fixture(64)), [0, 1, 2])
        after = plan(enumerate_batches(fixture(128)), [0, 1, 2])
        self.assertEqual(before["增量当前迁移预算字节"], 128 << 20)
        self.assertEqual(before["静态差额字节（非实测收益）"], 64 << 20)
        self.assertEqual(after["静态差额字节（非实测收益）"], 0)
        self.assertFalse(before["保持原门槛可入选"])
        self.assertEqual(before["身份"], "未执行的假设组合")

    def test_expiration_costs_are_not_mixed_between_runs(self):
        self.assertEqual(plan(enumerate_batches(fixture(64)), [0, 2, 3])["静态差额字节（非实测收益）"], 160 << 20)
        self.assertEqual(plan(enumerate_batches(fixture(128)), [0, 2, 3])["静态差额字节（非实测收益）"], 96 << 20)

    def test_bad_capacity_or_duplicate_group_is_rejected(self):
        for key, value in (("当前有效字节", 0), ("当前有效字节", ZONE_BYTES),
                           ("当前无效字节", 0), ("组号", 0), ("基线路径窗口内同代RR字节", -1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                rows = fixture(64)
                rows[2][key] = value
                enumerate_batches(rows)

    def test_empty_selection_is_rejected(self):
        rows = fixture(64)
        for row in rows:
            row["原门槛选中"] = False
        with self.assertRaises(ValueError):
            enumerate_batches(rows)
        with self.assertRaises(ValueError):
            enumerate_batches([])

    def test_enumeration_does_not_mutate_input(self):
        rows = fixture(64)
        saved = copy.deepcopy(rows)
        enumerate_batches(rows)
        self.assertEqual(rows, saved)

    def test_output_refuses_existing_directory(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                write_reports(Path(root), [])
            self.assertEqual(list(Path(root).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
