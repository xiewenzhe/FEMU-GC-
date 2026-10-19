"""尾部专项的合成回归；合成字段不是设备结果。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_guarded_tail import check_ongoing,check_fallback,compare,MIB,SECOND


class GuardedTailAnalysis(unittest.TestCase):
    def reads(self):
        return [{"序号":str(i),"计划纳秒":str(i*SECOND//32),
                 "开始纳秒":str(i*SECOND//32+1000),"API耗时纳秒":"100000","字节":"4096"}
                for i in range(1120)]

    def decision(self):
        return {"原会改选":"1","GreedyZone":"100","短期建议Zone":"200","实际Zone":"100",
            "实际改选":"0","原额外有效字节":str(16*MIB),"预测达阈值纳秒":str(2*SECOND),
            "尾部分类":"未知回退","尾部允许":"0","复核后可改选":"0",
            "尾部原因":"Greedy稀疏头边界未知，建议首片段确定装不入"}

    def fallback(self,d):
        return check_fallback([d],{"冷Zone":"100","热Zone":"200"})

    def pair(self):
        common=dict(核验通过=True,软件身份={"示例":"同一身份"},持续读计划哈希="示例",
            首次选区前布局={"示例":"相同起点"},终态布局={"经过纳秒":"35000000000","示例":"相同终态"},
            全程迁移MiB=288)
        return (dict(common,策略="greedy",配置={"策略":"greedy","示例":"固定参数"}),
            dict(common,策略="guarded",配置={"策略":"guarded","示例":"固定参数"}))

    def test_fixed_ongoing(self):
        self.assertEqual(len(check_ongoing(self.reads())),64)

    def test_rate_changed_rejected(self):
        r=self.reads();r[32]["计划纳秒"]="1"
        with self.assertRaises(ValueError):check_ongoing(r)

    def test_count_changed_rejected(self):
        with self.assertRaises(ValueError):check_ongoing(self.reads()[:-1])

    def test_early_read_rejected(self):
        r=self.reads();r[32]["开始纳秒"]="1"
        with self.assertRaises(ValueError):check_ongoing(r)

    def test_negative_duration_rejected(self):
        r=self.reads();r[0]["API耗时纳秒"]="-1"
        with self.assertRaises(ValueError):check_ongoing(r)

    def test_window_changed_rejected(self):
        r=self.reads();r[-1]["开始纳秒"]=str(36*SECOND)
        with self.assertRaises(ValueError):check_ongoing(r)

    def test_unknown_fallback(self):
        self.assertEqual(self.fallback(self.decision())["实际改选"],"0")

    def test_no_proposal_cannot_prove_fallback(self):
        with self.assertRaises(ValueError):check_fallback([],{"冷Zone":"100","热Zone":"200"})

    def test_false_pressure_claim_rejected(self):
        d=self.decision();d["尾部分类"]="预留压力保守拦截"
        with self.assertRaises(ValueError):self.fallback(d)

    def test_false_execution_rejected(self):
        d=self.decision();d["实际改选"]="1";d["实际Zone"]="200"
        with self.assertRaises(ValueError):self.fallback(d)

    def test_budget_changed_rejected(self):
        d=self.decision();d["原额外有效字节"]=str(17*MIB)
        with self.assertRaises(ValueError):self.fallback(d)

    def test_not_recent_rejected(self):
        d=self.decision();d["预测达阈值纳秒"]=str(11*SECOND)
        with self.assertRaises(ValueError):self.fallback(d)

    def test_duplicate_proposal_rejected(self):
        d=self.decision()
        with self.assertRaises(ValueError):check_fallback([d,d],{"冷Zone":"100","热Zone":"200"})

    def test_pair_same_final_state(self):
        self.assertTrue(compare(*self.pair())["终态布局一致"])

    def test_pair_final_difference_reported(self):
        a,b=self.pair();b["终态布局"]={"经过纳秒":"35000000001","示例":"不同终态"}
        self.assertFalse(compare(a,b)["终态布局一致"])

    def test_pair_software_difference_rejected(self):
        a,b=self.pair();b["软件身份"]={"示例":"不同身份"}
        with self.assertRaises(ValueError):compare(a,b)

    def test_pair_parameter_difference_rejected(self):
        a,b=self.pair();b["配置"]={"策略":"guarded","示例":"不同参数"}
        with self.assertRaises(ValueError):compare(a,b)


if __name__=="__main__":unittest.main()
