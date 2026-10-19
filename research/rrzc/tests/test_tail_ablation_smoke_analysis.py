"""消融配对比较的合成回归；示例指标不代表真实SSD结果。"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_tail_ablation_smoke import compare, expected_software


class TailAblationSmokeAnalysis(unittest.TestCase):
    def pair(self):
        common = dict(核验通过=True,配置={"示例": "固定"},软件身份={"示例": "同库"},
            完整核心身份={"示例头": "同源"},持续读计划哈希="示例",首次选区前布局={"示例": "同起点"},
            全程迁移MiB=288,重复文件复制MiB=0)
        return dict(copy.deepcopy(common),模式="keep"), dict(copy.deepcopy(common),模式="bypass")

    def test_zero_benefit_is_valid(self):
        r = compare(*self.pair())
        self.assertTrue(r["核验通过"])
        self.assertEqual(r["旁路净迁移增加MiB"], 0)

    def test_negative_benefit_is_reported_not_hidden(self):
        a, b = self.pair()
        b.update(全程迁移MiB=320,重复文件复制MiB=32)
        r = compare(a, b)
        self.assertEqual((r["旁路净迁移增加MiB"], r["旁路净重复复制增加MiB"]), (32, 32))

    def test_mode_order_must_be_explicit(self):
        a, b = self.pair()
        with self.assertRaises(ValueError):
            compare(b, a)

    def test_pair_requires_same_inputs_and_software(self):
        for k in ("配置", "软件身份", "完整核心身份", "持续读计划哈希", "首次选区前布局"):
            with self.subTest(key=k), self.assertRaises(ValueError):
                a, b = self.pair()
                b[k] = "示例差异"
                compare(a, b)


if __name__ == "__main__":
    unittest.main()
