"""无建议占位符不能被计为真实选区差异。"""
import unittest
from analyze_sampling_scope import suggestion_difference


class SuggestionTest(unittest.TestCase):
    def test_optional_zone_identity(self):
        cases = ((0, 2**64-1, 0, 0, False),
                 (1, 7, 1, 7, False), (1, 7, 1, 9, True),
                 (0, 2**64-1, 1, 7, True), (1, 9, 0, 0, True))
        for a, x, b, y, expected in cases:
            with self.subTest(模型存在=a, Greedy存在=b, 模型Zone=x, GreedyZone=y):
                self.assertEqual(suggestion_difference(dict(模型存在=a, 模型Zone=x,
                                                           Greedy存在=b, GreedyZone=y)), expected)


if __name__ == '__main__':
    unittest.main()
