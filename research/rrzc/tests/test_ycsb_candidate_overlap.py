"""候选交集离线诊断的合成回归；示例不是设备结果。"""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_ycsb_candidate_overlap import MIB, analyze, funnel, same_epoch


def candidate(valid=32*MIB, rc=2000, delta=0, eta=(1 << 64)-1,
              eligible=True, covered=True, target=True, known=True):
    return dict(轮次='1', GreedyZone='1', 行号=100,
        空间={'Zone': '2', '有效字节': str(valid), '容量字节': str(256*MIB),
              '覆盖资格': str(int(covered)), '空目标资格': str(int(target))},
        同块={'当前RC': str(rc), '原候选资格': str(int(eligible)), '统计准入': '1'},
        增长={'增量可知': '1', '真实增量': str(delta), '间隔纳秒': '1000000000'},
        短期={'短期预测可知': str(int(known)), '短期达阈值纳秒': str(eta)})


class CandidateOverlap(unittest.TestCase):
    def test_known_zero_is_not_missing_sample(self):
        result, growing = funnel([candidate()], 192*MIB, 10**10)
        self.assertEqual(result['合格且短期预测可知'], 1)
        self.assertEqual(result['合格且近期无增长'], 1)
        self.assertEqual(result['合格且近期紧迫'], 0)
        self.assertEqual(growing, [])

    def test_space_and_coverage_failures_overlap(self):
        result, _ = funnel([candidate(valid=95*MIB, rc=15000, eta=0,
            eligible=False, covered=False)], 192*MIB, 10**10)
        self.assertEqual(result['未满足回收预算'], 1)
        self.assertEqual(result['覆盖资格不通过'], 1)
        self.assertEqual(result.get('合格且近期紧迫', 0), 0)

    def test_threshold_inclusive_and_greedy_distinction(self):
        row = candidate(rc=4000, delta=10, eta=10**10)
        result, growing = funnel([row], 192*MIB, 10**10)
        self.assertEqual(result['合格非Greedy且近期紧迫'], 1)
        self.assertEqual(growing[0]['ETA秒'], 10)
        row['GreedyZone'] = '2'
        result, _ = funnel([row], 192*MIB, 10**10)
        self.assertEqual(result['合格且近期紧迫'], 1)
        self.assertEqual(result['合格非Greedy且近期紧迫'], 0)

    def test_unknown_eta_not_treated_as_urgent(self):
        result, _ = funnel([candidate(eta=0, known=False)], 192*MIB, 10**10)
        self.assertEqual(result['合格且近期紧迫'], 0)

    def test_inconsistent_eligibility_rejected(self):
        with self.assertRaises(ValueError):
            funnel([candidate(valid=100*MIB)], 192*MIB, 10**10)

    def test_reset_and_unstable_epochs_rejected(self):
        original = {'重置窗稳定': '1', '主机代际': '2/2/2', '观察序号': '4/4/4'}
        self.assertTrue(same_epoch(original, copy.deepcopy(original)))
        for key, value in (('重置窗稳定', '0'), ('主机代际', '3/3/3'),
                           ('主机代际', '2/2/3'), ('观察序号', '6/6/6')):
            changed = dict(original, **{key: value})
            self.assertFalse(same_epoch(original, changed))
        self.assertFalse(same_epoch(original, {}))

    def test_incomplete_or_empty_log_rejected(self):
        with self.assertRaises(ValueError):
            analyze([])
        with self.assertRaises(ValueError):
            analyze(['2026/10/19-01:40:00.000000 1 简单双因素候选：Zone=1'])


if __name__ == '__main__':
    unittest.main()
