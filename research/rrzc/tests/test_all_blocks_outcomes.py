"""预测回溯的代际、干预和原文件集合边界；合成日志不作为实验收益。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_all_blocks_outcomes import classify


class OutcomeTest(unittest.TestCase):
    def candidate(self, selected=False):
        return dict(决策行=10, 决策时刻=100, 主机代际=2, 当轮选中=selected,
                    文件初始字节={11: 4, 12: 5})

    def event(self, kind='RR', line=20, generation=2):
        return dict(类型=kind, 行=line, 时刻=103, 主机代际=generation,
                    完成=True, 复制={12: 5}, 有效字节=5, 编号=1)

    def test_selected_zone_is_censored_at_current_zc(self):
        out = classify(self.candidate(True), [self.event()], {11: (15, 102)}, 110)
        self.assertEqual(out['后续结局'], '当轮ZC介入')
        self.assertIsNone(out['后续RR编号'])
        self.assertEqual(out['介入前已退役原文件数'], 0)

    def test_reset_stops_reused_zone_rr(self):
        out = classify(self.candidate(), [self.event('Reset'), self.event(line=30, generation=3)],
                       {11: (15, 101), 12: (16, 102)}, 110)
        self.assertEqual(out['后续结局'], '先Reset')
        self.assertTrue(out['介入前全部原文件退役'])
        self.assertIsNone(out['后续RR编号'])

    def test_partial_retirement_then_same_epoch_rr(self):
        out = classify(self.candidate(), [self.event()], {11: (15, 101)}, 110)
        self.assertEqual(out['后续RR有效字节'], 5)
        self.assertTrue(out['RR原文件子集核验'])
        self.assertFalse(out['介入前全部原文件退役'])

    def test_unrelated_files_are_rejected(self):
        event = self.event()
        event['复制'] = {99: 5}
        with self.assertRaisesRegex(ValueError, '原满区文件集合'):
            classify(self.candidate(), [event], {}, 110)

    def test_missing_reset_is_not_rr_opportunity(self):
        out = classify(self.candidate(), [self.event(generation=3)], {}, 110)
        self.assertEqual(out['后续结局'], '代际无法衔接')
        self.assertIsNone(out['后续RR编号'])

    def test_no_event_is_only_finite_censoring(self):
        out = classify(self.candidate(), [], {}, 110)
        self.assertEqual(out['后续结局'], '业务结束截尾')
        self.assertEqual(out['到事件或截止秒'], 10)


if __name__ == '__main__':
    unittest.main()
