"""无WAL试次的单变量和隔离边界检查，不代表设备实验结果。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_ycsb_nowal_host as run

class NowalTest(unittest.TestCase):
    def test_formal_only_adds_wal_switch(self):
        old=run.properties(run.TESTS/'workloada-zns.properties')
        new=run.properties(run.TESTS/'workloada-zns-nowal.properties')
        self.assertEqual(new,dict(old,**{'rocksdb.disable_wal':'true'}))
    def test_pilot_keeps_full_dataset(self):
        full=run.properties(run.TESTS/'workloada-zns-nowal.properties')
        pilot=run.properties(run.TESTS/'workloada-zns-nowal-pilot.properties')
        self.assertEqual(pilot,dict(full,operationcount='40000'))
    def test_new_overlay_does_not_reuse_failed_guest(self):
        self.assertEqual(run.PORT,'8094')
        self.assertEqual(run.PARENT.name,'ycsb-retention-20261017-build.qcow2')
        self.assertNotEqual(run.PARENT,run.BUILD_IMAGE)
        self.assertEqual(run.LABEL,'ycsb-nowal-20261017')
    def test_binding_defaults_to_wal_enabled(self):
        patch=(run.TESTS/'ycsb-nowal.patch').read_text()
        self.assertIn('GetProperty("rocksdb.disable_wal","false")',patch)
        self.assertNotIn('fs_zenfs.cc',patch)

if __name__=='__main__':unittest.main()
