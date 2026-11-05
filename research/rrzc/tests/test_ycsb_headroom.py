"""压实空间留量与不可变工具去重测试；不伪造业务结果。"""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from run_ycsb_headroom_host import HeadroomRun,RECORDS,OPERATIONS
from run_ycsb_quick_host import QuickRun,wrapper_source,profile8
from dedup_ycsb_archived_tools import link_identical
from ycsb_af import digest


class HeadroomTest(unittest.TestCase):
    def test_scale_does_not_change_default_old_trial(self):
        self.assertEqual(QuickRun.record_count,1572864)
        self.assertEqual(HeadroomRun.record_count,1048576)
        self.assertEqual(RECORDS*4096,4<<30)
        self.assertEqual(OPERATIONS,300000)
        self.assertEqual(profile8()['命名空间']['容量字节'],8<<30)

    def test_same_wrapper_keeps_thresholds(self):
        text=wrapper_source()
        self.assertIn('ZENFS_RR_STRICT=1',text)
        self.assertIn('GC启动百分比=20',text)
        self.assertIn('RR阈值=1024',text)

    def test_dry_run_preserves_separate_files(self):
        with tempfile.TemporaryDirectory() as folder:
            a,b=Path(folder)/'a',Path(folder)/'b'
            a.write_text('相同的已结束工具');b.write_bytes(a.read_bytes())
            self.assertGreater(link_identical(a,b,digest(a)),0)
            self.assertNotEqual(a.stat().st_ino,b.stat().st_ino)

    def test_hardlink_keeps_bytes_and_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            a,b=Path(folder)/'a',Path(folder)/'b'
            a.write_text('相同的已结束工具');b.write_bytes(a.read_bytes())
            expected=digest(a);link_identical(a,b,expected,apply=True)
            self.assertTrue(a.exists() and b.exists())
            self.assertEqual(a.stat().st_ino,b.stat().st_ino)
            self.assertEqual(digest(b),expected)

    def test_changed_file_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            a,b=Path(folder)/'a',Path(folder)/'b'
            a.write_text('工具甲');b.write_text('工具乙');before=digest(b)
            with self.assertRaises(ValueError):link_identical(a,b,digest(a),apply=True)
            self.assertEqual(digest(b),before)

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            a,b=Path(folder)/'a',Path(folder)/'b'
            a.write_text('工具');b.symlink_to(a)
            with self.assertRaises(ValueError):link_identical(a,b,digest(a),apply=True)


if __name__=='__main__':unittest.main()
