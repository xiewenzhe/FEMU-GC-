"""新固定900秒入口的合成回归；只检查实验和比较约束，不伪造设备收益。"""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_tail_ablation_medium import compare
from analyze_guarded_stream import analyze
from run_guarded_stream_host import main as host_main


class TailAblationMediumTests(unittest.TestCase):
    def pair(self):
        window = dict(开始秒=180,结束秒=900,ZC次数=10,RR次数=5,总迁移MiB=240,
            首次迁移MiB=208,重复迁移MiB=32,维护源Reset=15,全部IO区Reset=15)
        common = dict(核验通过=True,配置={"示例": "固定参数"},负载配置="original",软件身份={"示例": "同库"},
            完整核心身份={"示例": "同源"},每线程请求轨迹SHA256={"示例": "相同请求"},预热后计量=window,
            锚点维护账目={},总维护迁移MiB=288,终态空间={"经过纳秒": "900000000000", "空闲字节": "1024"},
            采样完整性核验={"逐块轨迹覆盖达标": True})
        return dict(copy.deepcopy(common),消融模式="keep"), dict(copy.deepcopy(common),消融模式="bypass")

    def test_zero_benefit_is_valid(self):
        r = compare(*self.pair())
        self.assertEqual(r["旁路全程迁移增加MiB"], 0)

    def test_rr_reduction_does_not_hide_extra_copy(self):
        a, b = self.pair()
        b["预热后计量"].update(ZC次数=15,RR次数=0,总迁移MiB=272,重复迁移MiB=64)
        d = compare(a, b)["预热后净差（旁路减保留）"]
        self.assertEqual((d["RR次数"], d["ZC次数"], d["总迁移MiB"], d["重复迁移MiB"]), (-5, 5, 32, 32))

    def test_space_loss_is_reported(self):
        a, b = self.pair(); b["终态空间"]["空闲字节"] = "0"
        self.assertFalse(compare(a, b)["两组终态空间一致"])

    def test_coverage_loss_is_reported_separately(self):
        a, b = self.pair(); b["采样完整性核验"]["逐块轨迹覆盖达标"] = False
        r = compare(a, b)
        self.assertTrue(r["核验通过"])
        self.assertFalse(r["两组逐块覆盖均达标"])

    def test_changed_pair_inputs_refused(self):
        for key in ("配置", "负载配置", "软件身份", "完整核心身份", "每线程请求轨迹SHA256"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                a, b = self.pair(); b[key] = "示例变化"
                compare(a, b)

    def test_failed_run_cannot_be_compared(self):
        a, b = self.pair(); b["核验通过"] = False
        with self.assertRaises(ValueError):
            compare(a, b)

    def test_changed_window_refused(self):
        a, b = self.pair(); b["预热后计量"]["开始秒"] = 0
        with self.assertRaises(ValueError):
            compare(a, b)

    def test_non_execute_or_non_medium_entry_refused_before_access(self):
        for args in (["greedy", "--medium", "--tail-ablation", "keep"],
                     ["guarded", "--tail-ablation", "bypass"]):
            with self.subTest(args=args), self.assertRaises(RuntimeError):
                host_main(args)

    def test_old_directory_cannot_use_new_protocol(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)/"guarded-medium-20261015-guarded"; run.mkdir()
            (run/"config.csv").write_text("策略\nguarded\n")
            with self.assertRaisesRegex(ValueError, "消融目录"):
                analyze(run, tail_ablation="bypass")


if __name__ == "__main__":
    unittest.main()
