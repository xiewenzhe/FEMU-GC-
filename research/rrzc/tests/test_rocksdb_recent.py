"""独立采样分析的合成边界检查，不是设备实验结果。"""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_rocksdb_recent import sampling_audit
from analyze_rocksdb_observe import analyze
import test_rocksdb_pressure as pressure_tests

class RecentTest(unittest.TestCase):
    def fixture(self):
        lines=pressure_tests.PressureTest().frame()
        lines[-2]='独立约束决策：轮次=1，存在Greedy=1，GreedyZone=805306368'
        lines.insert(0,'独立近期采样：可用=1，查询纳秒=100，主机结束纳秒=1000')
        lines.insert(1,'独立近期采样冻结：轮次=1，发布序号=1')
        for zone in (805306368,1073741824):
            lines.insert(-2,f'V1增长候选：轮次=1，Zone={zone}，增量可知=1，真实增量=10，间隔纳秒=1000000000，原因=同块近期增量支持界限内达阈值')
        return lines
    def test_known_same_block_and_greedy_counts(self):
        r=sampling_audit(self.fixture())
        self.assertEqual((r['合格候选增量可知数'],r['Greedy增量可知数']),(2,1))
    def test_actual_samples_inside_rr_window(self):
        lines=self.fixture()
        lines[:0]=['RR 开始：测试窗口','独立近期采样：可用=1','RR 统计：测试窗口']
        self.assertEqual(sampling_audit(lines)['RR期间采样'][0]['内部采样数'],1)
    def test_incomplete_rr_or_missing_freeze_rejected(self):
        with self.assertRaises(ValueError):sampling_audit(['RR 开始：测试窗口']+self.fixture())
        with self.assertRaises(ValueError):sampling_audit([s for s in self.fixture() if '独立近期采样冻结：' not in s])
    def test_out_of_window_known_rejected(self):
        with self.assertRaises(ValueError):sampling_audit([s.replace('间隔纳秒=1000000000','间隔纳秒=3000000000') for s in self.fixture()])
    def test_unknown_not_zero_or_known(self):
        lines=[s.replace('增量可知=1','增量可知=0').replace('原因=同块近期增量支持界限内达阈值','原因=最新采样过近') for s in self.fixture()]
        r=sampling_audit(lines)
        self.assertEqual(r['合格候选增量可知数'],0)
        self.assertEqual(r['未知原因'],{'最新采样过近':2})
    def test_recent_profile_requires_explicit_analyzer(self):
        with self.assertRaises(ValueError):analyze(Path('rocksdb-recent-20261016'),{})
        with self.assertRaises(ValueError):analyze(Path('rocksdb-recent-20261016'),{},pressure=True,recent=True)

if __name__=='__main__':unittest.main()
