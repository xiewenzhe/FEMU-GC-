"""合成日志测试只核验同块增量准入，不代表真实SSD收益。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_v1_growth_pair import check_gate


class GrowthAnalysis(unittest.TestCase):
    def setUp(self):
        self.now={"统计准入":"1","设备会话":"1/2","PPA":"0/0/0/0","设备代际":"0",
            "块编号":"0","代际起点纳秒":"0","Zone":str(2*256*1024*1024),
            "窗口标志":"7","快照纳秒":"40000000000","当前RC":"4040"}
        self.before={"增量可知":"1","增长准入":"1","有前点":"1","前会话":"1/2",
            "前PPA":"0/0/0/0","前代际":"0","前块编号":"0","前代际起点纳秒":"0",
            "前区编号":"2","前标志":"7","前快照纳秒":"39000000000","前RC":"4024",
            "间隔纳秒":"1000000000","真实增量":"16","短期达阈值纳秒":"3500000000"}

    def test_continues(self):
        self.assertTrue(check_gate(self.now,self.before))

    def test_stops(self):
        self.before.update(增长准入="0",前RC="4040",真实增量="0",短期达阈值纳秒=str(2**64-1))
        self.assertFalse(check_gate(self.now,self.before))

    def test_slow(self):
        self.before.update(增长准入="0",前RC="4036",真实增量="4",短期达阈值纳秒="14000000000")
        self.assertFalse(check_gate(self.now,self.before))

    def test_unknown_cannot_pass(self):
        self.before["增量可知"]="0"
        with self.assertRaises(ValueError): check_gate(self.now,self.before)
        self.before["增长准入"]="0"
        self.assertFalse(check_gate(None,self.before))

    def test_different_identity(self):
        for key in ("前会话","前PPA","前代际","前块编号","前代际起点纳秒","前区编号"):
            changed=dict(self.before); changed[key]="99"
            with self.subTest(key=key),self.assertRaises(ValueError): check_gate(self.now,changed)

    def test_wrong_increment(self):
        self.before["真实增量"]="15"
        with self.assertRaises(ValueError): check_gate(self.now,self.before)

    def test_stale(self):
        self.before["前快照纳秒"]="35000000000"
        with self.assertRaises(ValueError): check_gate(self.now,self.before)

    def test_wrong_prediction(self):
        self.before["短期达阈值纳秒"]="3500000001"
        with self.assertRaises(ValueError): check_gate(self.now,self.before)


if __name__=="__main__": unittest.main()
