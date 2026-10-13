"""合成日志仅用于解析器测试，不是实验结果。"""

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "analyze_maintenance_overlap.py"
SPEC = importlib.util.spec_from_file_location("maintenance_overlap", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def log(second, message):
    return f"2026/10/11-00:00:{second:02d}.000000 7 {message}"


def candidate(zone=256, selected=0, invalid=1, unknown=False):
    pressure = "读压力未知" if unknown else (
        "RR读数阈值 16，最大读数 112，读数总和 5287，风险块 80%/90%/100%=64/64/64")
    return log(0, f"ZC 候选：Zone {zone}，无效比例 {invalid}%，有效数据 100 字节，{pressure}，选中 {selected}")


def summary(selected=0):
    return log(0, f"GC 选区汇总：检查空闲比例=18%，无效比例门槛=94%，满Zone=1，最大无效比例=98%，选中Zone={selected}，迁移片段=0")


def reclaim(second=10, zone=256, attempt=1, error=False):
    lines = [log(second, f"RR 开始：Zone {zone}，达阈值物理块 64，有效数据 100 字节，封闭剩余容量 0 字节"),
             log(second, f"RR 统计：尝试 {attempt} 次，本次迁移 100 字节，本次重置 {0 if error else 1} 个 Zone，累计迁移 100 字节")]
    lines.append(log(second, "[ERROR] 主机端 RR 检查失败：NotFound: 迁移期间文件已被删除") if error
                 else log(second, f"RR 完成：Zone {zone} 已重置，累计完成 1 次"))
    return lines


def observation(stage="ZC候选", second=0, generation=0, sequence=0, stable=1,
                covered=100, non_sst=0, pressure=1, at=64):
    complete = int(covered == 100)
    eligible = int(complete and not non_sst)
    return log(second, f"Zone 维护观测：阶段={stage}，Zone=256，"
               f"主机代际={generation}/{generation}/{generation}，观察序号={sequence}/{sequence}/{sequence}，"
               f"重置窗稳定={stable}，快照写指针=512，满区=1，有效字节=100，覆盖字节={covered}，"
               f"含非SST={non_sst}，覆盖完整={complete}，SST覆盖资格={eligible}，"
               f"读压力可用={pressure}，达阈值块={at}，迁移准入=未检查")


class MaintenanceOverlapTest(unittest.TestCase):
    def parse(self, extra=(), first=None):
        return MODULE.parse_lines([first or candidate(), summary(), *extra], 64)

    def test_nearest_event_is_only_observation(self):
        relation = MODULE.relate_candidates(self.parse(reclaim()))[0]
        self.assertEqual(relation["delta"], 10)
        self.assertEqual(relation["rr"]["status"], "完成")
        self.assertFalse(relation["cuts"])
        self.assertIn("尚未完整确认", relation["status"])

    def test_empty_allocation_excludes_reuse(self):
        extra = [log(5, "[DEBUG] Allocating zone(new=1) start: 0x100 wp: 0x100 lt: 3"), *reclaim()]
        relation = MODULE.relate_candidates(self.parse(extra))[0]
        self.assertEqual(len(relation["cuts"]), 1)
        self.assertIn("排除", relation["status"])

    def test_old_candidate_cannot_count_later_generations_again(self):
        parsed = self.parse([*reclaim(), *reclaim(second=20, attempt=2)])
        relation = MODULE.relate_candidates(parsed)[0]
        self.assertEqual(relation["rr"]["attempt"], 1)
        self.assertEqual(len(MODULE.relate_candidates(parsed)), 1)

    def test_orphan_rr_completion_is_rejected(self):
        lines = [candidate(selected=1, invalid=98), summary(1),
                 log(5, "RR 完成：Zone 256 已重置，累计完成 1 次")]
        with self.assertRaises(ValueError):
            MODULE.parse_lines(lines, 64)

    def test_reverse_links_exclude_later_rr_generations(self):
        parsed = self.parse([*reclaim(), *reclaim(second=20, attempt=2)])
        relations = MODULE.relate_reclaims(parsed)
        self.assertFalse(relations[0]["cuts"])
        self.assertEqual(relations[1]["cuts"][0]["reason"], "RR 完成并重置")

    def test_reverse_links_do_not_use_future_candidate(self):
        parsed = self.parse(reclaim(zone=512))
        relation = MODULE.relate_reclaims(parsed)[0]
        self.assertIsNone(relation["candidate"])
        self.assertIn("此前未观察", relation["status"])

    def test_reserve_replacement_is_empty_boundary(self):
        relation = MODULE.relate_candidates(self.parse([
            log(5, "迁移预留替换：旧Zone=512，新Zone=256"), *reclaim()]))[0]
        self.assertEqual(relation["cuts"][0]["reason"], "空替补接管预留")

    def test_successful_reset_diagnostic_is_boundary(self):
        relation = MODULE.relate_candidates(self.parse([
            log(5, "Zone 重置诊断：Zone=256，状态=成功，主机代际=0/1，观察序号=2，代际可信=1"),
            *reclaim()]))[0]
        self.assertEqual(relation["cuts"][0]["reason"], "主机成功 Reset：代际 0/1")

    def test_bad_successful_generation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "代际没有递增"):
            self.parse([log(5, "Zone 重置诊断：Zone=256，状态=成功，主机代际=0/0，观察序号=2")])

    def test_failed_reset_is_not_counted_as_successful_boundary(self):
        relation = MODULE.relate_candidates(self.parse([
            log(5, "Zone 重置诊断：Zone=256，状态=失败，主机代际=0/0，观察序号=2，代际可信=0"),
            *reclaim()]))[0]
        self.assertFalse(relation["cuts"])
        self.assertIn("Reset 失败", relation["status"])

    def diagnostic_relation(self, first=None, last=None, extra=()):
        parsed = MODULE.parse_lines([first or observation(), candidate(), summary(), *extra,
                                     last or observation("RR开始", second=10), *reclaim()], 64)
        return MODULE.relate_candidates(parsed)[0]

    def test_stable_matching_generation_is_only_local_reset_evidence(self):
        relation = self.diagnostic_relation()
        self.assertIn("同一主机 Reset 代际", relation["status"])
        self.assertIn("非原子数据快照", relation["status"])

    def test_stable_endpoints_with_changed_generation_are_excluded(self):
        relation = self.diagnostic_relation(last=observation("RR开始", second=10, generation=1, sequence=2))
        self.assertIn("不同，排除", relation["status"])

    def test_changed_sequence_even_same_generation_is_excluded(self):
        relation = self.diagnostic_relation(last=observation("RR开始", second=10, sequence=2))
        self.assertIn("不同，排除", relation["status"])

    def test_unstable_endpoint_is_not_promoted(self):
        relation = self.diagnostic_relation(first=observation(stable=0))
        self.assertIn("不稳定", relation["status"])

    def test_failed_reset_prevents_generation_confirmation(self):
        relation = self.diagnostic_relation(extra=[log(5, "Zone 重置诊断：Zone=256，状态=失败，主机代际=0/0，观察序号=2，代际可信=0")])
        self.assertIn("Reset 失败", relation["status"])

    def test_candidate_coverage_is_preserved_not_a_new_policy_filter(self):
        relation = self.diagnostic_relation(first=observation(covered=0))
        self.assertEqual(relation["candidate"]["diagnostic"]["sst_eligible"], 0)
        self.assertIn("同一主机", relation["status"])

    def test_stable_odd_reset_sequence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "稳定标记"):
            self.diagnostic_relation(first=observation(sequence=1))

    def test_inconsistent_coverage_flag_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "SST 覆盖资格"):
            self.diagnostic_relation(first=observation().replace("覆盖完整=1", "覆盖完整=0"))

    def test_unavailable_pressure_is_not_cold(self):
        parsed = MODULE.parse_lines([observation(pressure=0, at=0), candidate(unknown=True), summary()], 64)
        self.assertEqual(parsed["candidates"][0]["diagnostic"]["pressure_available"], 0)
        self.assertIsNone(parsed["candidates"][0]["at"])

    def test_candidate_and_diagnostic_pressure_must_match(self):
        with self.assertRaisesRegex(ValueError, "候选与维护观测"):
            self.diagnostic_relation(first=observation(at=1))

    def test_non_sst_cannot_be_reported_as_rr_start(self):
        with self.assertRaisesRegex(ValueError, "RR 开始与维护观测"):
            self.diagnostic_relation(last=observation("RR开始", second=10, non_sst=1))

    def test_unpaired_diagnostic_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未完整配对"):
            MODULE.parse_lines([observation()], 64)

    def test_wrong_diagnostic_zone_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未完整配对"):
            self.diagnostic_relation(first=observation().replace("Zone=256", "Zone=1024"))

    def test_unknown_pressure_remains_unknown(self):
        parsed = self.parse(first=candidate(unknown=True))
        self.assertIsNone(parsed["candidates"][0]["at"])
        self.assertIn("窗口结束", MODULE.relate_candidates(parsed)[0]["status"])

    def test_notfound_is_not_completion_and_cost_is_preserved(self):
        rr = self.parse(reclaim(error=True))["rr"][0]
        self.assertEqual(rr["status"], "中止")
        self.assertEqual(rr["moved"], 100)
        self.assertEqual(rr["resets"], 0)

    def test_inconsistent_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不符合当前空间门槛"):
            MODULE.parse_lines([candidate(selected=1), summary(1)], 64)

    def test_truncated_candidate_or_rr_is_rejected(self):
        for lines in ([candidate()], [*reclaim()[:-1]]):
            with self.assertRaises(ValueError):
                MODULE.parse_lines(lines, 64)

    def test_wrong_geometry_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "几何参数"):
            MODULE.parse_lines([candidate(), summary()], 32)

    def test_output_directory_must_be_new(self):
        with tempfile.TemporaryDirectory(prefix="rrzc-overlap-test-") as directory:
            with self.assertRaisesRegex(ValueError, "输出目录已存在"):
                MODULE.write_reports(Path(directory), [])


if __name__ == "__main__":
    unittest.main()
