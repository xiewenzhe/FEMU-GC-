"""滚动分析器的计划、配对和稳态筛查单元测试；这里不产生实验数字。"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_stream import expected_reads, check_trace, stability, validate_pair, MARKER


def operations(seconds):
    result = []
    for g in range(seconds//30):
        for kind, anch, ver in (("cold", 16, 240), ("hot", 32, 224)):
            for suffix, count in (("anchor.sst", anch), ("version0.bin", ver)):
                for i in range(count):
                    result.append({"线程": "0", "序号": str(len(result)), "阶段": "更新写", "操作": "写1MiB",
                                   "代号": str(g), "文件": f"/rrzc-stream/g{g}.{kind}.{suffix}",
                                   "偏移": str(i*(1 << 20)), "字节": str(1 << 20), "成功": "1"})
    for seq, (_, g, kind, phase, offset) in enumerate(expected_reads(seconds)):
        result.append({"线程": "1", "序号": str(seq), "阶段": phase, "操作": "读4KiB", "代号": str(g),
                       "文件": f"/rrzc-stream/g{g}.{'hot' if kind else 'cold'}.anchor.sst",
                       "偏移": str(offset), "字节": "4096", "成功": "1"})
    return result


class StreamTests(unittest.TestCase):
    def test_clock_uses_commit_not_query_start(self):
        self.assertIsNone(MARKER.search("滚动负载时钟：经过纳秒=100"))
        self.assertEqual(MARKER.search("滚动更新提交：代号=0，经过纳秒=100")[1], "100")

    def test_plan_cutoff(self):
        requests = expected_reads(60)
        self.assertTrue(all(r[0] < 60*10**9 for r in requests))
        self.assertEqual(sum(r[1] == 0 for r in requests), (3584+172)*2)

    def test_hot_cold_offsets(self):
        requests = expected_reads(60)
        self.assertEqual({r[4] for r in requests if r[2]}, {0})
        self.assertEqual({r[4] for r in requests if not r[2]}, {i*65536 for i in range(8)})

    def test_trace_complete(self):
        self.assertEqual(len(check_trace(operations(60), 60)), 2)

    def test_trace_rejects_missing_read(self):
        with self.assertRaises(ValueError):
            check_trace(operations(60)[:-1], 60)

    def test_trace_rejects_wrong_write_offset(self):
        data = operations(60)
        data[0]["偏移"] = "4096"
        with self.assertRaises(ValueError):
            check_trace(data, 60)

    def test_trace_rejects_failure(self):
        data = operations(60)
        data[0]["成功"] = "0"
        with self.assertRaises(ValueError):
            check_trace(data, 60)

    def test_zero_rr_allowed_only_weighted_scale(self):
        window = {"平均空闲字节": 0, "平均有效字节": 0, "平均满区可回收字节": 0,
                  "ZC成功次数": 20, "RR完成次数": 0, "总维护迁移字节": 0}
        self.assertTrue(stability([window]*6, "weighted")["维护规模目标达到"])
        self.assertFalse(stability([window]*6, "valid")["维护规模目标达到"])

    def test_short_run_not_stable(self):
        self.assertFalse(stability([], "valid")["相对稳定筛查通过"])

    def test_drift_rejected(self):
        window = {"平均空闲字节": 0, "平均有效字节": 0, "平均满区可回收字节": 0,
                  "ZC成功次数": 20, "RR完成次数": 20, "总维护迁移字节": 0}
        data = [dict(window) for _ in range(6)]
        data[-1]["平均空闲字节"] = 257*(1 << 20)
        self.assertFalse(stability(data, "valid")["相对稳定筛查通过"])

    def test_pair_checks_trace_not_space_equality(self):
        a = {"配置": {"策略": "valid"}, "软件身份": "样例身份", "每线程实际轨迹sha256": ["样例轨迹"],
             "终态空间": {"空闲字节": 0, "有效字节": 0, "满区可回收字节": 0, "部分区未回收无效字节": 0}}
        b = copy.deepcopy(a)
        b["配置"]["策略"] = "weighted"
        b["终态空间"]["空闲字节"] = 1
        self.assertFalse(validate_pair([a, b])["终态空间一致"])
        b["每线程实际轨迹sha256"] = ["不同轨迹"]
        with self.assertRaises(ValueError):
            validate_pair([a, b])


if __name__ == "__main__":
    unittest.main()
