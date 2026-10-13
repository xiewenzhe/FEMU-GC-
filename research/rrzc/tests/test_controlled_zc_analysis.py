"""合成四格数据只用于核验器测试，不是设备实验结果。"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_controlled_zc import validate_initial


def fixture():
    sources = [(256 * (i + 1), (32 if i < 2 else 128) << 20) for i in range(4)]
    rows = []
    for i, (zone, size) in enumerate(sources):
        rows.append({"组号": i, "读取分布": "八块分散" if i % 2 else "单块集中",
                     "Zone字节地址": zone, "有效字节": size, "真实删除字节": (256 << 20) - size,
                     "累计4KiB读次数": 8192, "最大读数": 1024 if i % 2 else 8192,
                     "读数总和": 8192, "达阈值块": 0 if i % 2 else 1, "空闲比例": 10})
    return rows, sources


class ControlledZCAnalysisTest(unittest.TestCase):
    def test_four_independent_cells(self):
        validate_initial(*fixture())

    def test_duplicate_source_is_rejected(self):
        rows, sources = fixture()
        sources[1] = sources[0]
        with self.assertRaises(ValueError):
            validate_initial(rows, sources)

    def test_missing_row_is_rejected(self):
        rows, sources = fixture()
        with self.assertRaises(ValueError):
            validate_initial(rows[:-1], sources)

    def test_below_threshold_is_not_eight_at_threshold(self):
        rows, sources = fixture()
        rows[3]["达阈值块"] = 8
        with self.assertRaises(ValueError):
            validate_initial(rows, sources)

    def test_space_condition_must_match(self):
        rows, sources = fixture()
        rows[0]["空闲比例"] = 21
        with self.assertRaises(ValueError):
            validate_initial(rows, sources)

    def test_changed_budget_or_validity_is_rejected(self):
        baseline, sources = fixture()
        for key in ("有效字节", "真实删除字节", "累计4KiB读次数", "最大读数", "读数总和"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                rows = copy.deepcopy(baseline)
                rows[2][key] += 1
                validate_initial(rows, sources)


if __name__ == "__main__":
    unittest.main()
