"""合成输入仅检查独立重算内核，不是设备实验或机制收益。"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_v1_shadow_stream import eta, predict, MAX, SECOND


def candidate(zone,valid):
    return {"Zone":str(zone),"有效字节":str(valid)}


def block(rc=4000,d10=200,d30=600,flags=7):
    return {"当前RC":str(rc),"十秒真实增量":str(d10),"三十秒真实增量":str(d30),"窗口标志":str(flags)}


class ShadowAnalysis(unittest.TestCase):
    def setUp(self):
        self.candidates = [candidate(1,16<<20),candidate(2,32<<20)]
        self.blocks = {1:block(100,0,0),2:block()}

    def test_urgent_with_budget(self):
        result = predict(self.candidates,self.blocks)
        self.assertEqual(result[:5],(1,2,True,16<<20,4800000000))

    def test_high_rc_not_urgent(self):
        self.blocks[2]=block(2952,20,60)
        self.assertEqual(eta(self.blocks[2]),572*SECOND)
        self.assertFalse(predict(self.candidates,self.blocks)[2])

    def test_budget_reject(self):
        self.candidates[1]=candidate(2,48<<20)
        self.assertEqual(predict(self.candidates,self.blocks)[5],"紧迫候选超出额外有效搬运预算")

    def test_greedy_itself_urgent(self):
        self.blocks[1]=block()
        self.assertEqual(predict(self.candidates,self.blocks)[5],"Greedy自身已经紧迫")

    def test_unknown_window(self):
        self.blocks[1]=block(100,0,0,1)
        self.assertEqual(predict(self.candidates,self.blocks)[5],"Greedy窗口不完整")
        self.blocks.pop(1)
        self.assertEqual(predict(self.candidates,self.blocks)[5],"Greedy近期统计未知")

    def test_no_candidate(self):
        self.assertEqual(predict([],{}),(None,None,False,0,MAX,"没有合格候选"))

    def test_tie_and_rates(self):
        self.candidates.append(candidate(3,32<<20))
        self.blocks[3]=block()
        self.assertEqual(predict(self.candidates,self.blocks)[1],2)
        self.blocks[3]=block(4050)
        self.assertEqual(predict(self.candidates,self.blocks)[1],3)
        self.assertEqual(eta(block(4000,200,30)),96*SECOND)

    def test_actual_threshold_and_round_up(self):
        self.assertEqual(eta(block(4096,0,0,1)),0)
        self.assertEqual(eta(block(4095,3,9)),3333333334)
        self.assertEqual(eta(block(100,0,0)),MAX)
        self.assertIsNone(eta(block(4096,0,0,0)))


if __name__=="__main__":
    unittest.main()
