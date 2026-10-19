"""固定计划及配对核验的合成测试；不是真实设备结果。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_v1_pair import expected_plan, business_latency, compare


class PairAnalysis(unittest.TestCase):
    def test_continuous(self):
        p=expected_plan("continuous")
        reads=[r for r in p if r["类型"]=="0"]
        self.assertEqual(len(reads),768)
        self.assertEqual(int(reads[-1]["计划纳秒"]),47937500000)
        self.assertEqual(len([r for r in p if r["类型"]=="2"]),8)

    def test_stop(self):
        p=expected_plan("stop")
        self.assertEqual(len(p),18)
        self.assertEqual([r["计划纳秒"] for r in p if r["类型"]=="0"][-1],"437500000")
        self.assertFalse(any(r["类型"]=="4" for r in p))

    def test_retire(self):
        p=expected_plan("retire")
        self.assertEqual(len(p),19)
        self.assertEqual([r["计划纳秒"] for r in p if r["类型"]=="4"],["5000000000"])

    def test_latency_only_read_append(self):
        ops=[{"类型":str(k),"API耗时纳秒":str(n)} for k,n in ((0,1000),(0,3000),(2,9000),(1,999999),(3,999999))]
        self.assertEqual(business_latency(ops,0)["平均微秒"],2)
        self.assertEqual(business_latency(ops,2)["请求数"],1)

    def test_no_net_gain_despite_rr_less(self):
        a={"核验通过":True,"策略":"greedy","场景":"continuous","计划SHA256":"合成","初始布局":[],
           "软件身份":{},"RR次数":1,"ZC次数":1,"总维护迁移MiB":48}
        b=dict(a,策略="v1",RR次数=0,ZC次数=2)
        d=compare(a,b)
        self.assertEqual(d["RR减少次数"],1)
        self.assertEqual(d["总维护迁移变化MiB（V1减Greedy）"],0)

    def test_wrong_plan_rejected(self):
        a={"核验通过":True,"策略":"greedy","场景":"stop","计划SHA256":"合成A","初始布局":[],"软件身份":{}}
        b=dict(a,策略="v1",计划SHA256="合成B")
        with self.assertRaises(ValueError): compare(a,b)


if __name__=="__main__": unittest.main()
