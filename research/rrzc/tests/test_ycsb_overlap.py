"""两阶段隔离及机会判定的合成回归，不代表真实设备收益。"""
from pathlib import Path
import sys
import unittest
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from test_ycsb_read_pilot import props
import run_ycsb_overlap_host as run
from analyze_ycsb_overlap import opportunities
from analyze_ycsb_retention import measurements


class OverlapTest(unittest.TestCase):
    def test_same_keyspace_and_device_configuration(self):
        old=props('workload-read-latest-zns.properties')
        warm=props('workload-pressure-warmup-zns.properties')
        self.assertEqual({k for k in old.keys()|warm.keys() if old.get(k)!=warm.get(k)},
            {'operationcount','readproportion','updateproportion','requestdistribution','limit.ops'})
        self.assertEqual(warm['operationcount'],'1200000')
        self.assertEqual(warm['readproportion'],'0')
        self.assertEqual(warm['updateproportion'],'1')
        self.assertEqual(warm['requestdistribution'],'uniform')

    def test_fresh_build_parent_and_no_library_changes(self):
        self.assertEqual(run.PARENT.name,'ycsb-read-pilot-20261018-build.qcow2')
        self.assertNotEqual(run.PARENT,run.BUILD_IMAGE)
        text=(HERE/'build_ycsb_overlap_guest.sh').read_text()
        self.assertNotIn('git apply',text)
        self.assertNotIn('make DEBUG_LEVEL',text)
        self.assertIn('make -W core/db_factory.cc',text)
        self.assertIn("grep -Eq 'CC",text)
        self.assertIn('for arm in first second',text)

    def test_import_no_reload_and_no_intermediate_full_scan(self):
        text=(HERE/'run_ycsb_overlap_guest.sh').read_text()
        self.assertIn('flags=(-run)',text)
        self.assertIn('YCSB_AUDIT_KEYS=655360',text)
        self.assertEqual(text.count('"$directory/verify_ycsb"'),1)
        self.assertIn('ZENFS_RRZC_GUARDED_MODE=shadow',text)
        self.assertIn('ZENFS_RRZC_RECLAIM_BYTES=201326592',text)

    def test_warmup_metrics_must_be_explicit(self):
        text='1 sec: 7 operations; [UPDATE: Count=7 Avg=8.00 95=9.00 99=9.20 99.99=12.00]'
        self.assertEqual(measurements(text,7,('UPDATE',))['UPDATE']['p99微秒'],9.2)
        with self.assertRaises(ValueError):measurements(text,7)

    def fixture(self,*,valid=32,eta=5,zone=2,proposal=1):
        return [
            f'简单双因素候选：Zone={zone}，有效字节={valid<<20}，容量字节={256<<20}，覆盖资格=1，空目标资格=1',
            f'V1影子候选：Zone={zone}，块编号=1，当前RC=4000',
            f'独立约束候选：Zone={zone}，短期预测可知=1，短期达阈值纳秒={eta*10**9}',
            f'独立约束决策：轮次=1，存在Greedy=1，GreedyZone=1，原会改选={proposal}']

    def test_count_only_normal_eligible_near_rr_candidates(self):
        row=opportunities(self.fixture())
        self.assertEqual(row['包含非Greedy临RR候选轮数'],1)
        self.assertEqual(row['影子会改选轮数'],1)
        self.assertEqual(opportunities(self.fixture(valid=147))['包含临RR合格候选轮数'],0)
        self.assertEqual(opportunities(self.fixture(eta=11))['包含临RR合格候选轮数'],0)
        self.assertEqual(opportunities(self.fixture(zone=1))['包含非Greedy临RR候选轮数'],0)
        self.assertEqual(opportunities([])['影子会改选轮数'],0)
        with self.assertRaises(ValueError):opportunities(self.fixture()[:-1])


if __name__=='__main__':unittest.main()
