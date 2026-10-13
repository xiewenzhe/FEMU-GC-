"""合成事件仅为核验器单元测试，不是设备实验结果。"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_controlled_concurrent import MARKERS, marker_lines, validate_events
from analyze_controlled_zc import VALID


def fixture():
    sources = [(256 * (i + 1), VALID[i]) for i in range(4)]
    rr = []
    for i, line in ((0, 10), (2, 40), (3, 70)):
        rr.append({"zone": sources[i][0], "line": line, "end_line": line + 10,
                   "status": "完成", "moved": VALID[i], "valid": VALID[i], "resets": 1,
                   "tail": 0, "at": 8 if i == 3 else 1,
                   "diagnostic": {"sst_eligible": 1, "stable": 1, "generation_current": 0}})
    candidate = {"zone": sources[1][0], "line": 21, "selected": 1, "valid": VALID[1],
                 "diagnostic": {"sst_eligible": 1, "stable": 1, "generation_current": 0}}
    barriers = [{"zone": zone, "line": line, "reason": "主机成功 Reset：代际 0/1"}
                for (zone, _), line in zip(sources, (18, 35, 48, 78))]
    parsed = {"rr": rr, "candidates": [candidate], "barriers": barriers}
    return parsed, sources, (1, 60, 65, 90), [(1, VALID[1], 1)]


class ConcurrentAnalysisTest(unittest.TestCase):
    def test_classification_keeps_total_cost(self):
        kinds, _, zc, rr = validate_events(*fixture())
        self.assertEqual(kinds, {0: "RR", 1: "ZC", 2: "RR", 3: "RR"})
        self.assertEqual(zc, 32 << 20)
        self.assertEqual(rr, 288 << 20)
        self.assertEqual(zc + rr, 320 << 20)

    def test_rr_failure_or_partial_cost_is_rejected(self):
        for key, value in (("status", "未完成"), ("moved", 1), ("resets", 0), ("tail", 1), ("at", 8)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                args = fixture()
                args[0]["rr"][0][key] = value
                validate_events(*args)

    def test_future_rr_must_follow_fixed_pulse(self):
        args = fixture()
        args[0]["rr"][-1]["line"] = 61
        with self.assertRaises(ValueError):
            validate_events(*args)

    def test_duplicate_or_unknown_rr_source_is_rejected(self):
        for zone in (256, 999):
            with self.subTest(zone=zone), self.assertRaises(ValueError):
                args = fixture()
                args[0]["rr"][1]["zone"] = zone
                validate_events(*args)

    def test_coverage_and_generation_are_required(self):
        for key, value in (("sst_eligible", 0), ("stable", 0), ("generation_current", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                args = fixture()
                args[0]["candidates"][0]["diagnostic"][key] = value
                validate_events(*args)

    def test_actual_zc_statistics_are_required(self):
        args = fixture()
        with self.assertRaises(ValueError):
            validate_events(*args[:3], [(1, 64 << 20, 2)])

    def test_missing_or_duplicate_reset_is_rejected(self):
        args = fixture()
        args[0]["barriers"].pop()
        with self.assertRaises(ValueError):
            validate_events(*args)
        args = fixture()
        args[0]["barriers"].append(copy.deepcopy(args[0]["barriers"][0]))
        with self.assertRaises(ValueError):
            validate_events(*args)

    def test_marker_order_and_uniqueness(self):
        lines = ["受控同时维护：" + m for m in MARKERS]
        self.assertEqual(marker_lines(lines), [1, 2, 3, 4])
        for invalid in (lines[::-1], lines[:-1], lines + [lines[0]]):
            with self.subTest(lines=invalid), self.assertRaises(ValueError):
                marker_lines(invalid)


if __name__ == "__main__":
    unittest.main()
