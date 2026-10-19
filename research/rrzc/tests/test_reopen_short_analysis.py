"""合成交错日志回归；不代表设备线程栈或真实复现。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_reopen_lock_short import overlaps


class ReopenShortAnalysisTests(unittest.TestCase):
    def test_overlap_is_reported(self):
        self.assertEqual(overlaps(["MigrateFileExtents, fname: /a.sst, extent count: 1",
            "Reopen writable file: /a.sst"]), [{"文件": "/a.sst", "迁移开始行": 1, "重开开始行": 2}])

    def test_completed_copy_is_not_overlap(self):
        self.assertEqual(overlaps(["MigrateFileExtents, fname: /a.sst, extent count: 1",
            "MigrateFileExtents Finished, fname: /a.sst, extent count: 1",
            "Reopen writable file: /a.sst"]), [])

    def test_unrelated_file_is_not_overlap(self):
        self.assertEqual(overlaps(["MigrateFileExtents, fname: /a.sst, extent count: 1",
            "Reopen writable file: /b.sst"]), [])


if __name__ == "__main__":
    unittest.main()
