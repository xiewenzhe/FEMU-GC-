"""只用合成片段检查回退审计的边界；夹具不代表实测结果。"""
import unittest
from analyze_pair_fallback import partition, ZONE
from analyze_flush_retirement import next_generation_task


def copy(name, start, size, zone=ZONE):
    return {'字段': {'文件名':name,'源片段':str(start),'有效字节':str(size),'源Zone':str(zone)}}


class FallbackTest(unittest.TestCase):
    def test_physical_order_and_hot_exclusion(self):
        rows = [copy('a', ZONE + 10, 20), copy('h', ZONE + 30, 10), copy('b', ZONE + 40, 20)]
        self.assertEqual(partition(rows, {'h'}, ZONE, ZONE + 30),
                         dict(H之前写入且届时仍有效字节=20,H之后写入且届时仍有效字节=20))

    def test_crossing_or_out_of_zone_cannot_be_silently_classified(self):
        for row in (copy('x', ZONE + 20, 20), copy('x', 2 * ZONE - 1, 2),
                    copy('x', ZONE, 0), copy('x', ZONE, 1, 0)):
            with self.subTest(row=row), self.assertRaises(ValueError):
                partition([row], set(), ZONE, ZONE + 30)

    def test_reset_reuse_does_not_count_as_same_target_followup(self):
        tasks = [{'原始开始行':20,'Zone':ZONE,'源代际':2},
                 {'原始开始行':30,'Zone':ZONE,'源代际':3}]
        self.assertIsNone(next_generation_task(tasks, 10, ZONE, 1))
        self.assertEqual(next_generation_task(tasks, 10, ZONE, 2), tasks[0])
        self.assertIsNone(next_generation_task(tasks, 30, ZONE, 3))


if __name__ == '__main__':
    unittest.main()
