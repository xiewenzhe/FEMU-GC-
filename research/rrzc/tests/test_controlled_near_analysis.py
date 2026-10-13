"""合成数据仅用于核验器测试，不是设备实验。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_controlled_near import boundary, validate_future, validate_initial
from test_controlled_zc_analysis import fixture


def near_fixture():
    rows, sources = fixture()
    for i, row in enumerate(rows):
        row.update({"累计4KiB读次数": 3584, "读数总和": 3584, "最大读数": 448 if i % 2 else 3584, "达阈值块": 0})
    return rows, sources


def future_fixture(before):
    _, sources = near_fixture()
    rows = [{"组号": i, "读取Zone": sources[i][0] if i >= 2 else 999,
             "新增4KiB读次数": 512, "读数总和": 4096, "最大读数": 512 if i % 2 else 4096,
             "达阈值块": int(i == 2), "区有效字节": (64 if before else 128) << 20} for i in range(4)]
    return rows, sources, before


class NearAnalysisTest(unittest.TestCase):
    def test_all_initial_cells_below_threshold(self):
        validate_initial(*near_fixture())

    def test_already_triggered_or_changed_budget_is_rejected(self):
        for key, value in (("达阈值块", 1), ("累计4KiB读次数", 8192), ("最大读数", 4096)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                rows, sources = near_fixture()
                rows[2][key] = value
                validate_initial(rows, sources)

    def test_duplicate_or_missing_source_is_rejected(self):
        rows, sources = near_fixture()
        with self.assertRaises(ValueError):
            validate_initial(rows[:-1], sources)
        sources[3] = sources[2]
        with self.assertRaises(ValueError):
            validate_initial(rows, sources)

    def test_before_and_after_real_validity(self):
        for before in (False, True):
            validate_future(*future_fixture(before))

    def test_wrong_expiration_cost_or_reads_is_rejected(self):
        for key, value in (("区有效字节", 128 << 20), ("新增4KiB读次数", 1024), ("达阈值块", 8)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                args = future_fixture(True)
                args[0][2][key] = value
                validate_future(*args)

    def test_shared_target_is_not_original_source(self):
        args = future_fixture(True)
        args[0][0]["读取Zone"] = args[1][0][0]
        with self.assertRaises(ValueError):
            validate_future(*args)

    def test_boundary_requires_one_real_marker(self):
        self.assertEqual(boundary(["受控近阈值：补读"], "补读"), 1)
        for lines in ([], ["受控近阈值：补读"] * 2):
            with self.assertRaises(ValueError):
                boundary(lines, "补读")


if __name__ == "__main__":
    unittest.main()
