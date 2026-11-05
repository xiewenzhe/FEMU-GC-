"""验证空间账量纲与日志数量含义，防止把RR计数误读为块编号。"""
import unittest
from analyze_rr_replacement import net_space, parse_rr_start


class ReplacementTest(unittest.TestCase):
    def test_count_is_not_block_id(self):
        row = parse_rr_start('RR 开始：Zone 1207959552，达阈值物理块 1，有效数据 21145689 字节，封闭剩余容量 0 字节')
        self.assertEqual(row['达阈值物理块数量'], 1)
        self.assertIsNone(row['实际触发块编号'])

    def test_full_source_and_partial_source(self):
        cap, used = 128 << 20, 21146624
        self.assertEqual(net_space(cap, 0, used), 113071104)
        self.assertEqual(net_space(cap, cap - used, used), 0)
        with self.assertRaises(ValueError):
            net_space(cap, cap + 1, used)


if __name__ == '__main__':
    unittest.main()
