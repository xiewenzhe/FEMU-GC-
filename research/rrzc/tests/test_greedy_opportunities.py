"""机会回溯边界的合成单元测试；不作为设备实验结果。"""
import unittest
from analyze_greedy_opportunities import follow,rate


class OpportunityTests(unittest.TestCase):
    def candidate(self):return {'决策行':10,'主机代际':2}

    def event(self,kind,line,generation):
        return {'类型':kind,'行':line,'主机代际':generation}

    def test_reset_stops_reuse_match(self):
        events=[self.event('Reset',12,2),self.event('RR',20,3)]
        self.assertEqual(follow(self.candidate(),events)[0],'先Reset')

    def test_next_zc_is_not_rr_opportunity(self):
        events=[self.event('ZC',12,2),self.event('RR',20,2)]
        self.assertEqual(follow(self.candidate(),events)[0],'先后续ZC')

    def test_missing_reset_is_unknown(self):
        self.assertEqual(follow(self.candidate(),[self.event('RR',20,3)])[0],'代际无法衔接')

    def test_end_is_censored(self):
        self.assertEqual(follow(self.candidate(),[]),('业务结束截尾',None))

    def test_same_epoch_rr_only_after_decision(self):
        earlier=self.event('RR',9,2);later=self.event('RR',15,2)
        self.assertEqual(follow(self.candidate(),[earlier,later]),('后续同代际RR',later))

    def test_incomplete_window_is_not_zero(self):
        block={'窗口标志':'1'}
        self.assertIsNone(rate(block,10));self.assertIsNone(rate(block,30))

    def test_real_block_delta_not_max_difference(self):
        block={'窗口标志':'7','快照纳秒':str(31*10**9),'代际起点纳秒':'0',
            '十秒真实增量':'123','三十秒真实增量':'456','当前RC':'700'}
        self.assertEqual(rate(block,10),12.3);self.assertEqual(rate(block,30),15.2)

    def test_lost_history_cannot_make_rate(self):
        self.assertIsNone(rate({'窗口标志':'11'},10))


if __name__=='__main__':unittest.main()
