"""合成采样验证预注册质量规则；不当作设备运行或收益结果。"""
import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ablation_sampling import check_frames, audit_sampling, SECOND


def frames(count=1800):
    return [(i, i*SECOND//2+1000, i*SECOND//2+2000) for i in range(count)]


class AblationSamplingTests(unittest.TestCase):
    def check(self, data, gaps=None):
        return check_frames(data, 900, 900*SECOND+1000, gaps or [SECOND//2])

    def test_normal_target(self):
        r = self.check(frames())
        self.assertTrue(r["逐块轨迹覆盖达标"])
        self.assertEqual(r["实际批次"], 1800)

    def test_terminal_overrun_without_posthoc_switch(self):
        data = frames(1799)
        data[-1] = (1798, 899*SECOND+6000000, 899*SECOND+534000000)
        r = self.check(data, [SECOND//2, SECOND])
        self.assertTrue(r["逐块轨迹覆盖达标"])
        self.assertFalse(r["恰好理想批次"])
        self.assertEqual(r["补造或插值批次数"], 0)

    def test_large_device_gap_is_reported_unknown(self):
        self.assertFalse(self.check(frames(), [2*SECOND+1])["逐块轨迹覆盖达标"])

    def test_large_terminal_gap_is_not_covered(self):
        self.assertFalse(self.check(frames(1790))["逐块轨迹覆盖达标"])

    def test_late_observer_start_is_not_covered(self):
        self.assertFalse(self.check([(i, b+3*SECOND, e+3*SECOND) for i, b, e in frames(1790)])["逐块轨迹覆盖达标"])

    def test_internal_host_gap_is_not_hidden(self):
        data = frames()
        data = data[:10]+[(i-5, b, e) for i, b, e in data[15:]]
        self.assertFalse(self.check(data)["逐块轨迹覆盖达标"])

    def test_missing_serial_is_corrupt_not_quality_exception(self):
        with self.assertRaises(ValueError):
            self.check(frames()[1:])

    def test_overlapping_query_is_corrupt(self):
        data = frames(); data[2] = (2, 100, SECOND)
        with self.assertRaises(ValueError):
            self.check(data)

    def test_query_after_finish_is_corrupt(self):
        data = frames(); data[-1] = (1799, 899*SECOND, 901*SECOND)
        with self.assertRaises(ValueError):
            self.check(data)

    def test_snapshot_backwards_is_corrupt(self):
        with self.assertRaises(ValueError):
            self.check(frames(), [0])

    def fixture(self, directory, change=None):
        path = Path(directory)
        names = ["批次", "区索引", "查询开始纳秒", "查询结束纳秒", "设备会话高位", "设备会话低位", "RR阈值"]
        records = [dict(zip(names, (i, z, i*SECOND//2, i*SECOND//2+1, 1, 2, 4096)))
                   for i in range(2) for z in range(32)]
        if change:
            change(records)
        with (path/"recent.csv").open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=names)
            writer.writeheader(); writer.writerows(records)
        (path/"states.csv").write_text("经过纳秒\n900000000000\n")
        return path

    def test_complete_but_sparse_is_not_good_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            r = audit_sampling(self.fixture(d), {0: [{"time": 0}, {"time": SECOND//2}]}, {0: 1, 1: 1})
            self.assertTrue(r["采样协议核验通过"])
            self.assertFalse(r["逐块轨迹覆盖达标"])

    def test_partial_metadata_and_session_changes_refused(self):
        for change in (lambda r: r.pop(), lambda r: r[1].update(查询开始纳秒=1),
                       lambda r: r[32].update(设备会话高位=3), lambda r: r[1].update(区索引=0)):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
                audit_sampling(self.fixture(d, change), {0: [{"time": 0}, {"time": SECOND//2}]}, {0: 1, 1: 1})


if __name__ == "__main__":
    unittest.main()
