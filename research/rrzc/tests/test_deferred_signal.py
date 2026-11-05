"""合成样本验证时间边界和未来信息隔离，不代表真实实验效果。"""
from datetime import datetime, timedelta
import unittest

from analyze_deferred_signal import active_compactions, extrapolate, retirement_label


class DeferredSignalTest(unittest.TestCase):
    def test_incomplete_window_is_unknown(self):
        t = datetime(2026, 10, 28)
        self.assertIsNone(retirement_label([{'编号': 1, '字节': 10}],
            {1: t+timedelta(seconds=1)}, t, 10, t+timedelta(seconds=9)))

    def test_labels_count_only_original_cohort(self):
        t = datetime(2026, 10, 28)
        self.assertEqual(retirement_label([{'编号': 1, '字节': 10}],
            {1: t+timedelta(seconds=10), 2: t+timedelta(seconds=1)},
            t, 10, t+timedelta(seconds=11)), 10)

    def test_future_compaction_is_not_a_feature(self):
        t = datetime(2026, 10, 28)
        events = [(t+timedelta(seconds=1), 1,
            {'event': 'compaction_started', 'job': 1, 'files_L0': [3]})]
        self.assertEqual(active_compactions(events, t), set())
        self.assertEqual(active_compactions(events, t+timedelta(seconds=1)), {3})

    def test_unknown_retention_is_not_zero_prediction(self):
        self.assertIsNone(extrapolate({'留存可知': '0'}, 10, 10))

    def test_slower_rate_is_capped_and_does_not_use_labels(self):
        f = {'留存可知': '1', '短减少': '20', '短间隔': '1000000000',
             '长减少': '30', '长间隔': '3000000000'}
        self.assertEqual(extrapolate(f, 1000, 10), 100)
        self.assertEqual(extrapolate(f, 50, 10), 50)


if __name__ == '__main__':
    unittest.main()
