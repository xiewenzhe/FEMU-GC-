"""合成日志只验证真实决策独立核验，不能当作真实设备结果。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_v1_execution_smoke import decision_check, SECOND, MAX


class ExecutionAnalysis(unittest.TestCase):
    def setUp(self):
        self.candidates=[{"Zone":str(i+1),"有效字节":str(n<<20),"容量字节":str(256<<20),
            "覆盖资格":"1","空目标资格":"1"} for i,n in enumerate((16,32))]
        self.blocks={i:{"原候选资格":"1","统计准入":"1","当前RC":str(4032 if i==2 else 0),
            "十秒真实增量":str(4032 if i==2 else 0),"三十秒真实增量":str(4032 if i==2 else 0),"窗口标志":"7"} for i in (1,2)}
        self.decision={"存在Greedy":"1","GreedyZone":"1","建议Zone":"2","会改选":"1",
            "建议额外有效搬运字节":str(16<<20),"预测达阈值纳秒":"476190477","界限纳秒":str(10*SECOND),
            "预算字节":str(16<<20),"实际Zone":"2","实际改选":"1","实际额外有效搬运字节":str(16<<20),
            "原因":"近期紧迫且额外有效搬运在预算内","帧可用":"1"}

    def test_real_switch(self):
        self.assertTrue(decision_check(self.candidates,self.blocks,self.decision))

    def test_logged_zone_mismatch(self):
        self.decision["实际Zone"]="1"
        with self.assertRaises(ValueError): decision_check(self.candidates,self.blocks,self.decision)

    def test_extra_budget_mismatch(self):
        self.decision["实际额外有效搬运字节"]=str(32<<20)
        with self.assertRaises(ValueError): decision_check(self.candidates,self.blocks,self.decision)

    def test_frame_unknown(self):
        self.decision["帧可用"]="0"
        with self.assertRaises(ValueError): decision_check(self.candidates,self.blocks,self.decision)

    def test_read_window_mismatch(self):
        self.blocks[2]["三十秒真实增量"]="40"
        with self.assertRaises(ValueError): decision_check(self.candidates,self.blocks,self.decision)

    def test_no_eligible(self):
        self.candidates[0]["覆盖资格"]=self.candidates[1]["覆盖资格"]="0"
        for b in self.blocks.values(): b["原候选资格"]="0"
        d=dict(self.decision,存在Greedy="0",GreedyZone="0",建议Zone="0",会改选="0",
            建议额外有效搬运字节="0",预测达阈值纳秒=str(MAX),实际Zone="0",实际改选="0",
            实际额外有效搬运字节="0",原因="没有合格候选")
        self.assertFalse(decision_check(self.candidates,self.blocks,d))


if __name__=="__main__": unittest.main()
