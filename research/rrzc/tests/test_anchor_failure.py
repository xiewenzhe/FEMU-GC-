"""失败前缀的原块后续阈值核对；合成数据，不是设备结果。"""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_anchor_failure import threshold_outcome

class FailureFollowTest(unittest.TestCase):
    def block(self):return dict(快照纳秒='10000000000',当前RC='3800',设备会话='1/2',设备代际='0',块编号='3')
    def row(self,t,rc,**updates):
        r=dict(设备快照纳秒=str(t*10**9),RC=str(rc),会话高位='1',会话低位='2',设备代际后='0',统计稳定='1',块编号='3')
        r.update(updates);return r
    def test_observed_threshold_is_interval_not_exact_time(self):
        r=threshold_outcome(self.block(),[self.row(11,4000),self.row(12,4200)])
        self.assertTrue(r['阈值可核实']);self.assertEqual((r['确认未达阈值下界秒'],r['确认已达阈值上界秒']),(1,2))
    def test_cannot_skip_changed_block_and_then_find_old_block(self):
        rows=[self.row(11,4000,块编号='4'),self.row(12,4200)]
        self.assertFalse(threshold_outcome(self.block(),rows)['阈值可核实'])
    def test_reset_or_unstable_query_cannot_confirm_old_block(self):
        for change in (dict(设备代际后='1'),dict(会话低位='3'),dict(统计稳定='0')):
            self.assertFalse(threshold_outcome(self.block(),[self.row(11,4200,**change)])['阈值可核实'])
    def test_prefix_without_crossing_is_unknown_not_absence_of_rr(self):
        r=threshold_outcome(self.block(),[self.row(11,4000)])
        self.assertFalse(r['阈值可核实'])

if __name__=='__main__':unittest.main()
