"""读密集短测的负载、阈值和隔离范围回归，不代表设备结果。"""
from pathlib import Path
import sys
import unittest
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import run_ycsb_read_pilot_host as run


def props(name):
    return dict(row.split('=',1) for row in (HERE/name).read_text().splitlines()
                if '=' in row and not row.startswith('#'))


class ReadPilotTest(unittest.TestCase):
    def test_only_declared_workload_changes(self):
        old=props('workloada-zns-nowal-paced.properties')
        new=props('workload-read-latest-zns.properties')
        self.assertEqual({k for k in old.keys()|new.keys() if old.get(k)!=new.get(k)},
            {'operationcount','readproportion','updateproportion','requestdistribution','insertorder','zeropadding'})
        self.assertEqual(new['recordcount'],'655360')
        self.assertEqual(new['operationcount'],'600000')
        self.assertEqual(new['limit.ops'],'1000')
        self.assertEqual(new['readproportion'],'0.95')
        self.assertEqual(new['updateproportion'],'0.05')

    def test_fresh_stopped_parent(self):
        self.assertEqual(run.PARENT.name,'ycsb-space-emergency-20261018-build.qcow2')
        self.assertEqual(run.PORT,'8096')
        self.assertNotEqual(run.PARENT,run.BUILD_IMAGE)

    def test_actual_selection_stays_greedy(self):
        text=(HERE/'run_ycsb_read_pilot_guest.sh').read_text()
        self.assertIn('$1 == greedy',text)
        self.assertIn('ZENFS_RRZC_ALIGN_RECENT=1',text)
        self.assertIn('ZENFS_RRZC_SPACE_EMERGENCY=1',text)
        self.assertIn('ZENFS_RRZC_RECLAIM_BYTES=201326592',text)
        self.assertNotIn('zns_rr_threshold=',text)

    def test_relink_and_latest_workload_test(self):
        text=(HERE/'build_ycsb_read_pilot_guest.sh').read_text()
        self.assertIn('make -W core/db_factory.o',text)
        self.assertIn('latest-first latest-second',text)
        self.assertIn('-p rocksdb.fs_uri=',text)
        self.assertIn('test_rrzc_v1_growth.cc',text)


if __name__=='__main__':unittest.main()
