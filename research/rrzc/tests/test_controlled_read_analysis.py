"""合成记录只用于解析测试，不是设备实验结果。"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_controlled_read import validate_pressure


def synthetic_rows():
    # 固定阈值256的合成计划；整数80%/90%边界分别为205/231。
    rows = []
    for point, single_bands, eight_bands in (
            (0, (0, 0, 0), (0, 0, 0)),
            (64, (1, 1, 1), (0, 0, 0)),
            (128, (1, 1, 1), (0, 0, 0)),
            (204, (1, 1, 1), (0, 0, 0)),
            (230, (1, 1, 1), (8, 0, 0)),
            (256, (1, 1, 1), (8, 8, 8))):
        for kind, bands in enumerate((single_bands, eight_bands)):
            rows.append(dict(zip(("阶段轮数", "分布", "Zone字节地址", "有效字节",
                                  "累计4KiB读次数", "最大读数", "读数总和",
                                  "80%风险块", "90%风险块", "达阈值块"),
                                 (point, "八热块" if kind else "单热块", 512 if kind else 256,
                                  (128 if kind else 32) * (1 << 20), point * 8,
                                  point if kind else point * 8, point * 8, *bands))))
    return rows


class ControlledReadAnalysisTest(unittest.TestCase):
    def test_complete_plan(self):
        validate_pressure(synthetic_rows(), 256, (256, 512))

    def test_truncated_plan_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_pressure(synthetic_rows()[:-1], 256, (256, 512))

    def test_bad_fields_are_rejected(self):
        for key in ("最大读数", "读数总和", "有效字节", "累计4KiB读次数", "Zone字节地址", "达阈值块"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                rows = copy.deepcopy(synthetic_rows())
                rows[-1][key] += 1
                validate_pressure(rows, 256, (256, 512))

    def test_floor_point_does_not_reach_ceiling_band(self):
        rows = synthetic_rows()
        rows[7]["80%风险块"] = 8
        with self.assertRaises(ValueError):
            validate_pressure(rows, 256, (256, 512))

    def test_wrong_distribution_label_is_rejected(self):
        rows = synthetic_rows()
        rows[-1]["分布"] = "单热块"
        with self.assertRaises(ValueError):
            validate_pressure(rows, 256, (256, 512))


if __name__ == "__main__":
    unittest.main()
