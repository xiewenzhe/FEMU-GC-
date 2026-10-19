"""合成候选测试：成本排序诊断不越过紧迫度、资格和预算，也不伪造收益。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_model_scope import decision_features,MIB


class ModelScopeTests(unittest.TestCase):
    def fixture(self):
        candidates={z:dict(Zone=z,有效字节=str(v*MIB),容量字节=str(256*MIB),覆盖资格="1",空目标资格="1")
                    for z,v in (("1",16),("2",32))}
        costs={z:dict(短期预测可知="1",短期达阈值纳秒="5000000000") for z in candidates}
        d=dict(轮次="1",GreedyZone="1",短期建议Zone="2",界限纳秒="10000000000",
               预算字节=str(16*MIB),预测达阈值纳秒="5000000000")
        return candidates,costs,d

    def test_single_candidate_cannot_change_ranking(self):
        r=decision_features(*self.fixture())
        self.assertEqual(r["预算内紧迫候选数"],1)
        self.assertFalse(r["成本优先排序改变建议"])
        self.assertAlmostEqual(r["成本比值"],15/7)

    def test_cheaper_urgent_candidate_can_change_ranking(self):
        c,s,d=self.fixture();c["3"]=dict(c["2"],Zone="3",有效字节=str(24*MIB))
        s["3"]=dict(短期预测可知="1",短期达阈值纳秒="9000000000")
        self.assertTrue(decision_features(c,s,d)["成本优先排序改变建议"])

    def test_unknown_late_ineligible_or_over_budget_excluded(self):
        for issue in ("未知","不紧迫","无资格","超预算"):
            c,s,d=self.fixture();c["3"]=dict(c["2"],Zone="3",有效字节=str(24*MIB));s["3"]=dict(s["2"])
            if issue=="未知":s["3"]["短期预测可知"]="0"
            if issue=="不紧迫":s["3"]["短期达阈值纳秒"]="11000000000"
            if issue=="无资格":c["3"]["覆盖资格"]="0"
            if issue=="超预算":c["3"]["有效字节"]=str(48*MIB)
            with self.subTest(issue=issue):
                self.assertEqual(decision_features(c,s,d)["预算内紧迫候选数"],1)


if __name__=="__main__":unittest.main()
