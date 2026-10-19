"""合成候选及篡改日志测试，不作为SSD收益数据。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cost_rank_log import choose_cost,CostRankLog
from analyze_short_shadow_stream import choose_short

class CostRankTest(unittest.TestCase):
    def fixture(self):
        m=1<<20
        c=[dict(Zone=str(z),有效字节=str(v*m),容量字节=str(256*m),覆盖资格='1',空目标资格='1') for z,v in ((1,16),(2,24),(3,32))]
        e={1:100*10**9,2:8*10**9,3:4*10**9}
        return c,e,choose_short(c,e)
    def test_cost_beats_eta(self):
        c,e,o=self.fixture();self.assertEqual(o[1],3);self.assertEqual(choose_cost(c,e,o)[1],2)
    def test_no_override_when_greedy_urgent(self):
        c,e,_=self.fixture();e[1]=10**9;o=choose_short(c,e)
        self.assertEqual(choose_cost(c,e,o),o)
    def test_unknown_and_late_excluded(self):
        for eta in (None,11*10**9):
            c,e,_=self.fixture();e[2]=eta;o=choose_short(c,e)
            self.assertEqual(choose_cost(c,e,o)[1],3)
    def test_old_mode_rejects_new_log(self):
        with self.assertRaises(ValueError):CostRankLog(None).observe('成本排序：启用=0')
    def test_missing_and_tampered_record(self):
        c,e,o=self.fixture();r=CostRankLog(True)
        r.observe('成本排序：启用=1，默认ETA优先，仅在原紧迫准入集合内排序，尾部和源复核不变')
        with self.assertRaises(ValueError):r.resolve('1',c,e,o)
        r.pending={'轮次':'1'}
        with self.assertRaises(ValueError):r.resolve('1',c,e,o)
    def test_valid_record(self):
        c,e,o=self.fixture();r=CostRankLog(True)
        r.observe('成本排序：启用=1，默认ETA优先，仅在原紧迫准入集合内排序，尾部和源复核不变')
        r.observe('成本排序核验：轮次=1，启用=1，ETA建议Zone=3，生效建议Zone=2，排序改变=1，ETA预测纳秒=4000000000，生效预测纳秒=8000000000，ETA额外字节=16777216，生效额外字节=8388608')
        self.assertEqual(r.resolve('1',c,e,o)[1],2);r.finish(1)

if __name__=='__main__':unittest.main()
