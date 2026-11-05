"""防止把Reset后的RR或阈值块数量误当作同一块预测证据。"""
import unittest
from analyze_selector_opportunities import next_event,timeline


class OpportunityTest(unittest.TestCase):
    def test_reset_ends_old_candidate(self):
        lines=[
            '2026/11/04-12:00:00.000001 1 Zone 重置诊断：Zone=0，状态=成功，主机代际=8/9',
            '2026/11/04-12:00:01.000001 1 RR 开始：Zone 0，达阈值物理块 2，有效数据 32 字节',
        ]
        events=timeline(lines)[-1][0]
        self.assertEqual(next_event(events,0,0)['类型'],'Reset')
        self.assertEqual(next_event(events,1,1)['类型'],'RR')
        self.assertIsNone(next_event(events,2,1))

    def test_count_is_not_physical_block_identity(self):
        line='2026/11/04-12:00:01.000001 1 RR 开始：Zone 0，达阈值物理块 2，有效数据 32 字节'
        event=timeline([line])[-1][0][0]
        self.assertEqual(event['达阈值块数量'],2)
        self.assertIsNone(event['实际触发块编号'])

    def test_epoch_gap_is_not_silently_linked(self):
        with self.assertRaises(ValueError):
            next_event([dict(行=5,Reset序数=2,类型='RR')],1,1)


if __name__=='__main__':unittest.main()
