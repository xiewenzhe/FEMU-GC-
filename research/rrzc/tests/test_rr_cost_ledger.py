"""成本账本的合成边界检查，不是设备实验数据。"""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_rr_cost_ledger import current_cost,stable_generation,rr_followup,collect_context

class CostLedgerTest(unittest.TestCase):
    def candidate(self,valid,capacity=256):return dict(有效字节=valid,容量字节=capacity)
    def test_equal_capacity_has_one_correlated_byte_difference(self):
        r=current_cost(self.candidate(32),self.candidate(16))
        self.assertEqual(r['额外源有效搬运字节'],16)
        self.assertEqual(r['相对Greedy少回收逻辑字节'],16)
        self.assertEqual((r['候选逻辑净回收字节'],r['Greedy逻辑净回收字节']),(224,240))
        self.assertTrue(r['同容量时搬运差与回收差相等'])
        self.assertIsNone(r['已确认可避免RR字节']);self.assertIsNone(r['反事实净收益字节'])
    def test_greedy_cost_difference_is_zero_but_not_confirmed_benefit(self):
        r=current_cost(self.candidate(16),self.candidate(16))
        self.assertEqual(r['额外源有效搬运字节'],0)
        self.assertEqual(r['相对Greedy少回收逻辑字节'],0)
        self.assertIsNone(r['已确认可避免RR字节'])
    def test_different_capacity_does_not_force_equality(self):
        r=current_cost(self.candidate(32,512),self.candidate(16))
        self.assertFalse(r['同容量']);self.assertIsNone(r['同容量时搬运差与回收差相等'])
        self.assertEqual(r['相对Greedy少回收逻辑字节'],-240)
    def test_unit_reclaim_cost_is_exact_fraction_not_new_independent_signal(self):
        a=current_cost(self.candidate(32),self.candidate(16))
        b=current_cost(self.candidate(64),self.candidate(16))
        self.assertEqual(a['候选单位净回收逻辑搬运系数'],'1/7')
        self.assertEqual(b['候选单位净回收逻辑搬运系数'],'1/3')
    def test_invalid_amount_or_false_greedy_rejected(self):
        for value in (0,256,257,-1,8):
            with self.assertRaises(ValueError):current_cost(self.candidate(value),self.candidate(16))
    def test_unknown_host_epoch_not_matched_to_device_generation(self):
        self.assertEqual(stable_generation(dict(主机代际='7/7/7',重置窗稳定='1')),'7')
        self.assertIsNone(stable_generation(dict(主机代际='7/8/8',重置窗稳定='1')))
        self.assertIsNone(stable_generation(dict(主机代际='7/7/7',重置窗稳定='0')))
    def fixture(self):
        block=dict(行号=10,日志时钟='2026/10/16-12:00:00.000000')
        relation=dict(候选=dict(轮次=2,Zone='100',主机代际='7',行号=10),
            后续RR=dict(行号=20,Zone='100',主机代际='7'),同一主机代际=True)
        copies={20:dict(行号=20,Zone='100',开始时钟='2026/10/16-12:00:03.000000',实际迁移字节=12)}
        return block,relation,copies
    def test_actual_same_epoch_rr_does_not_become_counterfactual_saving(self):
        b,r,c=self.fixture();out=rr_followup('2','100','7',b,dict(同地址后续RR核对=[r]),c)
        self.assertEqual(out['观察内RR实际迁移字节'],12)
        self.assertEqual(out['观察内同代RR'][0]['距候选秒'],3)
        self.assertIn('不是改选后的反事实',out['因果边界'])
    def test_reused_address_unknown_epoch_and_missing_event_remain_unknown(self):
        b,r,c=self.fixture()
        for generation,relations in ((None,[r]),('8',[r]),('7',[])):
            out=rr_followup('2','100',generation,b,dict(同地址后续RR核对=relations),c)
            self.assertIsNone(out['观察内RR实际迁移字节'])
        r['同一主机代际']=False
        self.assertIsNone(rr_followup('2','100','7',b,dict(同地址后续RR核对=[r]),c)['观察内RR实际迁移字节'])
    def test_unpaired_or_reordered_rr_rejected(self):
        b,r,c=self.fixture()
        with self.assertRaises(ValueError):rr_followup('2','100','7',b,dict(同地址后续RR核对=[r]),{})
        c[20]['行号']=9
        with self.assertRaises(ValueError):rr_followup('2','100','7',b,dict(同地址后续RR核对=[r]),c)
        with self.assertRaises(ValueError):collect_context(['RR 统计：尝试 1 次，本次迁移 12 字节，本次重置 1 个 Zone，累计迁移 12 字节'])

if __name__=='__main__':unittest.main()
