"""滚动选区账目合成回归，不代表实际实验收益。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_v1_growth_stream import qualifies, empty_collection_count, same_generation_outcome, switch_ledger, compare


class GrowthStreamAnalysis(unittest.TestCase):
    def test_original_eligibility(self):
        c={"有效字节":str(32<<20),"容量字节":str(256<<20),"覆盖资格":"1","空目标资格":"1"}
        self.assertTrue(qualifies(c))
        for key,value in (("有效字节","0"),("有效字节",str(80<<20)),("覆盖资格","0"),("空目标资格","0")):
            with self.subTest(key=key,value=value): self.assertFalse(qualifies(dict(c,**{key:value})))

    def test_reset_ends_address_tracking(self):
        lines=["决策","Zone 重置诊断：Zone=1，状态=成功，代际=1","RR 开始：Zone 1，达阈值物理块 1，有效数据 16 字节，封闭剩余容量 0 字节"]
        self.assertEqual(same_generation_outcome(lines,1,1)["事件"],"源Reset，未先观察到RR或ZC选择")

    def test_rr_before_reset(self):
        lines=["决策","RR 开始：Zone 1，达阈值物理块 1，有效数据 16 字节，封闭剩余容量 0 字节","Zone 重置诊断：Zone=1，状态=成功，代际=1"]
        result=same_generation_outcome(lines,1,1)
        self.assertEqual(result["事件"],"独立RR开始")
        self.assertEqual(result["当时有效字节"],16)

    def test_no_event_unknown(self):
        self.assertIn("不能认定",same_generation_outcome(["决策"],1,1)["事件"])

    def test_original_budget_fallback(self):
        self.assertEqual(empty_collection_count([]),0)
        self.assertEqual(empty_collection_count([{"容量字节":str(256<<20),"有效字节":str(128<<20)}]),1)
        self.assertEqual(empty_collection_count([{"容量字节":str(256<<20),"有效字节":str(32<<20)}]),0)

    def test_zero_switch_has_no_cost_evidence(self):
        self.assertEqual(switch_ledger([{"实际改选":"0"}],[],[],[]),[])

    def test_ledger_uses_file_id_not_reused_zone(self):
        decision={"实际改选":"1","轮次":"1","GreedyZone":"1","实际Zone":"2","行号":10,"实际额外有效搬运字节":"16"}
        files=[{"轮次":"1","Zone":"1","文件编号":"8","文件名":"原文件","区内有效字节":"16"}]
        copies=[{"文件编号":"99","源Zone":"1","行号":12},{"文件编号":"8","源Zone":"3","行号":14}]
        detail=switch_ledger([decision],files,copies,[])[0]["被跳过文件后续观测"][0]
        self.assertEqual(detail["后续复制"],[copies[1]])

    def test_pair_checks_trace(self):
        a={"策略":"greedy","核验通过":True,"软件身份":{},"每线程请求轨迹SHA256":["合成A"],"配置":{"策略":"greedy"}}
        b=dict(a,策略="v1",每线程请求轨迹SHA256=["合成B"])
        with self.assertRaises(ValueError): compare(a,b)


if __name__=="__main__": unittest.main()
