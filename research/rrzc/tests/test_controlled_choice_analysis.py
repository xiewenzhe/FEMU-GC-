"""合成输入仅用于核验器单元测试，不是实验数据。"""
import copy
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_controlled_choice import (validate_counts, validate_pair, selection_view, zone_shape,
                                      validate_expiry_plan, validate_expiry_pair, validate_weighted_input, SOURCES, MIB)


def pair():
    state = {"空闲字节": 100, "有效字节": 200, "满区可回收字节": 300, "空闲比例": 10}
    return [{"条件": condition, "初始状态": ["合成状态"], "软件身份": ["合成身份"],
             "发送窗口": ["合成计划"], "实际业务读次数": {2: 768, 3: 768},
             "终态IO区形态": ["合成形态"],
             "空间账目": [copy.deepcopy(state) for _ in range(3)]} for condition in ("cold", "hot")]


def expiry_pair():
    runs = pair()
    for run in runs:
        run.update({"冷文件计划失效": True, "剩余64MiB与冷文件不存在校验": "通过",
                    "全部维护搬运字节": 128,
                    "失效计划": [{"失效组号": "3", "失效字节": "67108864",
                                  "失效阶段": "未来窗口后且追加写入前", "每文件未来业务读次数": "768",
                                  "追加写入字节": "536870912"}]})
        run["空间账目"].append(copy.deepcopy(run["空间账目"][-1]))
    return runs


class ChoiceAnalysisTest(unittest.TestCase):
    @staticmethod
    def weighted_records(mode):
        config = [{"模式": mode, "回收预算字节": str(192*MIB),
                   "有效量权重百分比": "100" if mode == "valid" else "50",
                   "块读数权重百分比": "0" if mode == "valid" else "50", "是否传入人工受害地址": "0"}]
        lines = []
        for group in (3, 2):
            maximum = 448 if group == 3 else 3584
            risk = maximum*1000000//4096
            lines.append(f"简单双因素候选：Zone={SOURCES[group]}，有效字节={64*MIB}，容量字节={256*MIB}，最大块读数={maximum}，读数阈值=4096，覆盖资格=1，空目标资格=1，读压力可用=1，空间分量ppm=750000，风险分量ppm={risk}，等权分数ppm={(750000+risk)//2}")
        chosen = SOURCES[3 if mode == "valid" else 2]
        score = 429687 if mode == "valid" else 812500
        lines.append(f"简单双因素实际集合：模式={mode}，原合格区=2，实际选中=1，Zone={chosen}，回收预算={192*MIB}，实际使用等权={int(mode=='weighted')}，等权分数ppm={score}")
        return config, lines, chosen

    def test_weighted_real_input_recomputes_both_modes(self):
        for mode in ("valid", "weighted"):
            config, lines, chosen = self.weighted_records(mode)
            with patch("analyze_controlled_choice.rows", return_value=config):
                result = validate_weighted_input(Path("/tmp/rrzc-仅合成测试不存在"), lines, "cold" if mode == "valid" else "hot", chosen, 4)
            self.assertEqual(result["简单双因素模式"], mode)

    def test_weighted_input_rejects_wrong_weights_or_score(self):
        config, lines, chosen = self.weighted_records("weighted")
        config[0]["块读数权重百分比"] = "40"
        with patch("analyze_controlled_choice.rows", return_value=config), self.assertRaises(ValueError):
            validate_weighted_input(Path("/tmp/rrzc-仅合成测试不存在"), lines, "hot", chosen, 4)
        config, lines, chosen = self.weighted_records("weighted")
        lines[1] = lines[1].replace("等权分数ppm=812500", "等权分数ppm=812501")
        with patch("analyze_controlled_choice.rows", return_value=config), self.assertRaises(ValueError):
            validate_weighted_input(Path("/tmp/rrzc-仅合成测试不存在"), lines, "hot", chosen, 4)

    def test_simple_selection_view_preserves_raw_summary(self):
        raw = self.lines()
        raw[-2] = "简单双因素实际集合：模式=weighted，原合格区=2，实际选中=1，Zone=512，回收预算=192"
        self.assertTrue(selection_view(raw)[-1].endswith("选中Zone=2"))
        self.assertTrue(raw[-1].endswith("选中Zone=1"))

    def test_expiry_pair_allows_more_free_space_and_reports_cost(self):
        runs = expiry_pair()
        runs[1]["空间账目"][-1]["空闲字节"] += 64
        runs[1]["全部维护搬运字节"] -= 64
        runs[1]["终态IO区形态"] = ["不同合成形态"]
        result = validate_expiry_pair(runs)
        self.assertTrue(result["热区优先终态空间是否不劣"])
        self.assertEqual(result["热区优先减去冷区优先的完整搬运字节"], -64)
        self.assertEqual(result["热区优先减去冷区优先的终态空闲字节"], 64)

    def test_expiry_pair_keeps_unfavorable_result(self):
        runs = expiry_pair()
        runs[1]["空间账目"][-1]["空闲字节"] -= 64
        self.assertFalse(validate_expiry_pair(runs)["热区优先终态空间是否不劣"])

    def test_expiry_pair_rejects_missing_duplicate_or_mixed_conditions(self):
        for runs in (expiry_pair()[:1], [expiry_pair()[0]]*2, pair()):
            with self.assertRaises(ValueError):
                validate_expiry_pair(runs)

    def test_expiry_pair_rejects_changed_inputs_or_live_payload(self):
        for key in ("初始状态", "软件身份", "发送窗口", "实际业务读次数", "失效计划"):
            runs = expiry_pair(); runs[1][key] = ["不同合成值"]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_expiry_pair(runs)
        for stage, key in ((0, "空闲字节"), (-1, "有效字节")):
            runs = expiry_pair(); runs[1]["空间账目"][stage][key] += 1
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                validate_expiry_pair(runs)

    def test_expiry_plan_requires_exact_unique_declaration(self):
        plan = expiry_pair()[0]["失效计划"]
        validate_expiry_plan(plan)
        for data in ([], plan*2, [dict(plan[0], 失效阶段="读取前")], [dict(plan[0], 失效字节="1")]):
            with self.assertRaises(ValueError):
                validate_expiry_plan(data)

    @staticmethod
    def zones():
        return [{"state": "EMPTY", "slba": i*524288, "wp": i*524288, "cap": 524288} for i in range(32)]

    def test_full_invalid_write_pointer_is_not_subtracted_from_address(self):
        a, b = self.zones(), self.zones()
        for zones, i in ((a, 4), (b, 8)):
            zones[i].update(state="FULL", wp=(1<<64)-1)
        self.assertEqual(zone_shape(a), zone_shape(b))
        self.assertIn(["FULL", 524288, 524288, 1], zone_shape(a))

    def test_zone_shape_rejects_invalid_geometry_or_write_pointer(self):
        for key, value in (("cap", 1), ("wp", (1<<64)-1), ("wp", 1)):
            zones = self.zones(); zones[3][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                zone_shape(zones)

    @staticmethod
    def lines():
        prefix = "2026/10/12-00:00:00.000000 1 "
        return [prefix + f"ZC 候选：Zone {zone}，无效比例 75%，有效数据 64 字节，读压力未知，选中 1"
                for zone in (256, 512)] + [prefix + "ZC实验单次选区：实际集合，指定Zone=512，原合格区=2，实际选中=1",
                                           prefix + "GC 选区汇总：选中Zone=1"]

    def test_selection_view_keeps_raw_and_uses_actual_choice(self):
        raw = self.lines(); view = selection_view(raw)
        self.assertTrue(raw[0].endswith("选中 1"))
        self.assertTrue(view[0].endswith("选中 1"))
        self.assertTrue(view[1].endswith("选中 1"))
        self.assertTrue(raw[-1].endswith("选中Zone=1"))
        self.assertTrue(view[-1].endswith("选中Zone=2"))
        self.assertEqual(len(raw), len(view))

    def test_deferred_selection_view_selects_none(self):
        raw = self.lines(); raw[-2] = raw[-2].replace("实际选中=1", "实际选中=0")
        raw[-1] = raw[-1].replace("选中Zone=1", "选中Zone=0")
        self.assertTrue(selection_view(raw)[-1].endswith("选中Zone=2"))

    def test_automatic_selection_view_without_manual_source(self):
        raw = self.lines()
        raw[-2] = "自动ZC实际集合：模式=rr，原合格区=2，实际选中=1，Zone=512，回收预算=192"
        self.assertTrue(selection_view(raw)[-1].endswith("选中Zone=2"))
        self.assertTrue(raw[-1].endswith("选中Zone=1"))

    def test_automatic_deferred_selection_view(self):
        raw = self.lines()
        raw[-2] = "自动ZC实际集合：模式=rr，原合格区=2，实际选中=0，状态=覆盖或空目标未就绪"
        raw[-1] = raw[-1].replace("选中Zone=1", "选中Zone=0")
        self.assertTrue(selection_view(raw)[-1].endswith("选中Zone=2"))

    def test_automatic_budget_fallback_with_one_original_candidate(self):
        raw = self.lines()[1:]
        raw[-2] = "自动ZC实际集合：模式=rr，原合格区=1，实际选中=1，状态=单区预算不可满足，退回原批量策略"
        self.assertTrue(selection_view(raw)[-1].endswith("选中Zone=1"))

    def test_automatic_selection_view_rejects_bad_count_or_address(self):
        for record in ("原合格区=1，实际选中=1，Zone=512", "原合格区=2，实际选中=3",
                       "原合格区=2，实际选中=1，Zone=768", "原合格区=2，实际选中=1"):
            raw = self.lines(); raw[-2] = "自动ZC实际集合：模式=rr，"+record
            with self.subTest(record=record), self.assertRaises(ValueError):
                selection_view(raw)

    def test_selection_view_rejects_wrong_original_count_or_source(self):
        for old, new in (("原合格区=2", "原合格区=1"), ("指定Zone=512", "指定Zone=768")):
            raw = self.lines(); raw[-2] = raw[-2].replace(old, new)
            with self.subTest(new=new), self.assertRaises(ValueError):
                selection_view(raw)

    def test_pair_accepts_different_window_space_but_equal_end(self):
        runs = pair(); runs[1]["空间账目"][1]["空闲字节"] = 50
        validate_pair(runs)

    def test_pair_rejects_initial_or_final_space_mismatch(self):
        for stage in (0, 2):
            runs = pair(); runs[1]["空间账目"][stage]["空闲字节"] = 50
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                validate_pair(runs)

    def test_pair_rejects_identity_plan_or_initial_state_mismatch(self):
        for key in ("初始状态", "软件身份", "发送窗口", "实际业务读次数", "终态IO区形态"):
            runs = pair(); runs[1][key] = ["不同合成值"]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_pair(runs)

    def test_pair_rejects_missing_or_duplicate_conditions(self):
        for runs in (pair()[:1], [pair()[0], pair()[0]]):
            with self.assertRaises(ValueError):
                validate_pair(runs)

    def test_actual_counts_pass(self):
        data = [{"组号": i, "累计业务4KiB读次数": 768, "窗口毫秒": 40000} for i in (2, 3)]
        self.assertEqual(validate_counts(data, 40), {2: 768, 3: 768})

    def test_actual_counts_reject_shortfall_wrong_group_or_short_window(self):
        for key, value in (("组号", 0), ("累计业务4KiB读次数", 767), ("窗口毫秒", 39999)):
            data = [{"组号": i, "累计业务4KiB读次数": 768, "窗口毫秒": 40000} for i in (2, 3)]
            data[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_counts(data, 40)


if __name__ == "__main__":
    unittest.main()
