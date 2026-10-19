"""机会筛选的合成边界验证；不把示例计数当成设备结果。"""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_rr_opportunities import adjacent_rate,snapshot_audit,maintenance_audit,CAPACITY,SECOND

class OpportunityTest(unittest.TestCase):
    def row(self,t,**updates):
        r={k:str(v) for k,v in dict(批次=t,阶段=1,Zone=805306368,会话高位=1,会话低位=2,
            设备代际前=0,设备代际后=0,主机代际前=0,主机代际后=0,主机序号前=0,主机序号后=0,
            统计稳定=1,块编号=3,窗口标志=1,查询开始纳秒=t*SECOND,查询结束纳秒=t*SECOND+10000,
            设备快照纳秒=t*SECOND+1,RC=3200+300*t,有效字节=32<<20,容量字节=CAPACITY,
            剩余字节=0,映射覆盖字节=32<<20).items()}
        r.update({k:str(v) for k,v in updates.items()});return r
    def mapping(self,row):return dict(批次=row['批次'],Zone=row['Zone'],文件='a.sst',区内有效字节=row['有效字节'])
    def test_positive_overlap_can_be_found(self):
        a,b=self.row(1),self.row(2)
        r=snapshot_audit([a,b],[self.mapping(b)])['分组']['阈值前近期紧迫']
        self.assertEqual((r['重复快照数'],r['区代际数'],r['基础资格观察数']),(1,1,1))
    def test_full_budget_and_coverage_are_separate(self):
        a=self.row(1)
        for change in (dict(剩余字节=4096),dict(有效字节=80<<20,映射覆盖字节=80<<20)):
            b=self.row(2,**change)
            r=snapshot_audit([a,b],[self.mapping(b)])['分组']['阈值前近期紧迫']
            self.assertEqual(r['基础资格观察数'],0)
        b=self.row(2);r=snapshot_audit([a,b],[])['分组']['阈值前近期紧迫']
        self.assertEqual(r['满区且预算满足数'],1);self.assertEqual(r['基础资格观察数'],0)
    def test_reset_or_max_block_switch_not_zone_max_difference(self):
        a=self.row(1)
        for changes in (dict(设备代际前=1,设备代际后=1),dict(块编号=4),dict(主机序号前=2,主机序号后=2)):
            self.assertFalse(adjacent_rate(a,self.row(2,**changes))['可知'])
    def test_unknown_never_synthesized_as_zero_rate(self):
        self.assertIsNone(adjacent_rate(None,self.row(1))['真实增量'])
        self.assertFalse(adjacent_rate(self.row(1),self.row(2,统计稳定=0))['可知'])
        r=adjacent_rate(self.row(1),self.row(2,RC=3500));self.assertTrue(r['可知']);self.assertIsNone(r['预测纳秒'])
    def test_time_window_and_rc_regression_rejected(self):
        a=self.row(1)
        for b in (self.row(2,RC=3499),self.row(4),self.row(2,查询开始纳秒=SECOND)):
            self.assertFalse(adjacent_rate(a,b)['可知'])
    def test_already_due_is_not_prethreshold_prevention(self):
        a,b=self.row(1),self.row(2,RC=4200)
        r=snapshot_audit([a,b],[self.mapping(b)])['分组']
        self.assertEqual(r['阈值前近期紧迫']['重复快照数'],0)
        self.assertEqual(r['当前已经达阈值']['基础资格观察数'],1)
    def test_repeat_snapshots_are_not_independent_epochs(self):
        a,b,c=self.row(1),self.row(2),self.row(3,RC=4000)
        r=snapshot_audit([a,b,c],[self.mapping(b),self.mapping(c)])['分组']['阈值前近期紧迫']
        self.assertEqual((r['重复快照数'],r['区代际数']),(2,1))
    def test_same_address_after_reset_is_not_same_candidate(self):
        lines=['Zone 维护观测：阶段=ZC候选，Zone=805306368，主机代际=1/1/1，重置窗稳定=1',
               'V1影子候选：轮次=1，Zone=805306368，原候选资格=1',
               'Zone 维护观测：阶段=RR开始，Zone=805306368，主机代际=3/3/3，重置窗稳定=1，有效字节=100000000，满区=1',
               'RR 开始：Zone 805306368，达阈值物理块 1，有效数据 100000000 字节，封闭剩余容量 0 字节']
        r=maintenance_audit(lines)
        self.assertFalse(r['同地址后续RR核对'][0]['同一主机代际'])
        self.assertFalse(r['RR启动事实'][0]['启动时回收预算满足'])
    def test_missing_rr_observation_or_duplicate_snapshot_rejected(self):
        with self.assertRaises(ValueError):maintenance_audit(['RR 开始：Zone 805306368，达阈值物理块 1，有效数据 100000000 字节，封闭剩余容量 0 字节'])
        a=self.row(1)
        with self.assertRaises(ValueError):snapshot_audit([a,a],[])

if __name__=='__main__':unittest.main()
