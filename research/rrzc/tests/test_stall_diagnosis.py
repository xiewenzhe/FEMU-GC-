"""停顿时序提取的合成回归；不代表实际SSD复现。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagnose_tail_ablation_stall import interleaving


class StallDiagnosisTests(unittest.TestCase):
    def fixture(self):
        return ["MigrateFileExtents, fname: /hot.sst, extent count: 1",
                "Reopen writable file: /hot.sst",
                "V1后续迁移复制：文件编号=39，复制成功=1，映射提交=尚未确认"]

    def test_single_interleaving(self):
        self.assertEqual(interleaving(self.fixture(), "/hot.sst", 39), (0, 1, 2))

    def test_prior_copy_cannot_be_joined_to_last_round(self):
        old = ["MigrateFileExtents, fname: /hot.sst, extent count: 1", "旧轮完成"]
        self.assertEqual(interleaving(old+self.fixture(), "/hot.sst", 39), (2, 3, 4))

    def test_missing_copy_rejected(self):
        with self.assertRaises(ValueError):
            interleaving(self.fixture()[:-1], "/hot.sst", 39)

    def test_other_file_id_rejected(self):
        with self.assertRaises(ValueError):
            interleaving(self.fixture(), "/hot.sst", 40)

    def test_completed_migration_cannot_be_called_stall(self):
        for ending in ("ReleaseMigrateZone: 0", "V1后续映射提交：文件编号=39，成功=1",
                       "MigrateFileExtents Finished, fname: /hot.sst"):
            with self.subTest(ending=ending), self.assertRaises(ValueError):
                interleaving(self.fixture()+[ending], "/hot.sst", 39)


if __name__ == "__main__":
    unittest.main()
