"""新入口合成回归：隔离旧结果，拒绝混库，归档故障不吞掉其他证据。"""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from analyze_guarded_stream import analyze as base_analyze
from analyze_tail_ablation_fixed import analyze, expected_software, LIBRARY, SOURCE, LABEL
from run_tail_ablation_fixed_pair import FixedPair


class FixedTests(unittest.TestCase):
    def test_expected_build_is_fixed(self):
        expected = expected_software()
        self.assertEqual(expected["librocksdb_debug.a"],LIBRARY)
        self.assertEqual(expected["plugin/zenfs/fs/fs_zenfs.cc"],SOURCE)
        self.assertIn("plugin/zenfs/fs/rrzc_tail_ablation.h",expected)

    def test_old_result_cannot_be_fixed_pair(self):
        with self.assertRaisesRegex(ValueError,"本轮修复版"):
            analyze(Path("tail-ablation-medium-20261015-keep/guarded-medium-20261015-guarded"),"keep")

    def test_fixed_results_require_explicit_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)/f"{LABEL}-keep"/"guarded-medium-20261015-guarded"
            run.mkdir(parents=True)
            (run/"config.csv").write_text("策略\nguarded\n")
            with self.assertRaisesRegex(ValueError,"消融目录"):
                base_analyze(run,tail_ablation="keep")
            with self.assertRaisesRegex(ValueError,"预先固定计划"):
                base_analyze(run,tail_ablation="keep",reopen_fixed=True)
            with self.assertRaisesRegex(ValueError,"明确登记"):
                base_analyze(run,reopen_fixed=True)

    def test_fixed_entry_rejects_old_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)/f"{LABEL}-keep"/"guarded-medium-20261015-guarded"
            run.mkdir(parents=True)
            (run/"config.csv").write_text("策略\nguarded\n")
            with self.assertRaisesRegex(ValueError,"消融目录"):
                base_analyze(run,tail_ablation="keep",reopen_fixed=True,allow_final_overrun=True)

    def test_archive_attempts_all_evidence_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            pair = FixedPair(SimpleNamespace(label=LABEL,seconds=900,space_weight=50),None,output)
            calls = []
            def command(argv,timeout):
                calls.append(argv)
                if len(calls) == 1:
                    raise RuntimeError("合成归档错误")
            pair.command = command
            pair.ssh = lambda query: "合成身份\n"
            errors = pair.archive(output,["scp"],"合成查询")
            self.assertEqual(len(calls),2)
            self.assertEqual(len(errors),1)
            self.assertEqual((output/"完整源码身份.after.sha256").read_text(),"合成身份\n")


if __name__ == "__main__":
    unittest.main()
