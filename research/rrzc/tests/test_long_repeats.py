"""合成日志仅核验旧日志账本，不代表任何实验收益。"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_long_repeats import parse_ledger, measured, summarize, compare, MIB

NAME = "/rrzc-stream/g0.cold.anchor.sst"
ORIGIN = datetime(2026, 10, 15).timestamp()


def log(message, second=0, thread=7):
    return f"2026/10/15-00:00:{second:02d}.000000 {thread} {message}"


def batch(source, target, generation=0, tail=256*MIB, second=1, name=NAME, kind="ZC", before=()):
    size = (32 if ".hot." in name else 16)*MIB
    if kind == "ZC":
        start = [f"ZC 候选：Zone {source}，无效比例 94%，有效数据 {size} 字节，RR读数阈值 4096，最大读数 1，读数总和 1，风险块 80%/90%/100%=0/0/0，选中 1",
                 f"简单双因素实际集合：实际选中=1，Zone={source}，",
                 "GC 选区汇总：选中Zone=1，"]
    else:
        start = [f"RR 开始：Zone {source}，达阈值物理块 1，有效数据 {size} 字节，封闭剩余容量 0 字节"]
    messages = [*start, f"MigrateFileExtents, fname: {name}, extent count: 1", *before,
                f"迁移使用专用预留：Zone={target}，需要={size}，剩余={tail}",
                f"TakeMigrateZone: {target}", f"ReleaseMigrateZone: {target}",
                f"MigrateFileExtents Finished, fname: {name}, extent count: 1",
                f"Zone 重置诊断：Zone={source}，状态=成功，主机代际={generation}/{generation+1}，代际可信=1"]
    messages.append(f"ZC 统计：轮次 1，本轮迁移 {size} 字节，本轮重置 1 个 Zone" if kind == "ZC" else
                    f"RR 统计：尝试 1 次，本次迁移 {size} 字节，本次重置 1 个 Zone，累计迁移 {size} 字节")
    return [log(m, second) for m in messages]


def seed():
    return [log(f"New writable file: {NAME} direct: 1")]


class LongRepeatsTests(unittest.TestCase):
    def parse(self, lines):
        return parse_ledger(lines, ORIGIN)

    def natural(self):
        return seed()+batch(256, 512, tail=16*MIB)+batch(512, 768, second=2)

    def test_first_is_not_repeat(self):
        result = self.parse(seed()+batch(256,512))
        self.assertEqual(summarize(result["复制"]), {"首次": {"次数":1,"MiB":16}})

    def test_natural_full_repeat_chain(self):
        result = self.parse(self.natural())
        self.assertEqual(result["复制"][1]["源封闭"]["类型"], "自然写满")
        self.assertEqual(summarize(result["复制"])["自然写满后ZC"]["MiB"],16)

    def test_replacement_does_not_itself_count_as_copy(self):
        lines = seed()+batch(256,512,tail=24*MIB)
        hot = "/rrzc-stream/g1.hot.anchor.sst"
        lines += [log(f"New writable file: {hot} direct: 1",2)]
        lines += batch(1024,768,second=3,name=hot,before=("迁移预留替换：旧Zone=512，新Zone=768",))
        result = self.parse(lines)
        self.assertEqual(len(result["复制"]),2)
        self.assertEqual(result["封闭"][0]["类型"],"提前替换Finish")
        self.assertNotIn("提前替换Finish后ZC",summarize(result["复制"]))
        result = self.parse(lines+batch(512,768,tail=224*MIB,second=4))
        self.assertEqual(result["复制"][-1]["源封闭"]["尾部字节"],8*MIB)

    def test_rr_on_partial_reserve(self):
        lines=seed()+batch(256,512)
        lines += [log(f"RR 封闭专用预留源：Zone=512，封闭剩余={240*MIB} 字节",2)]
        lines += batch(512,768,second=3,kind="RR")
        result=self.parse(lines)
        self.assertEqual(summarize(result["复制"])["预留源RR封闭后RR"]["MiB"],16)

    def test_same_address_new_generation_is_not_previous_data(self):
        lines = self.natural()
        lines.insert(len(seed()+batch(256,512,tail=16*MIB)),
                     log("Zone 重置诊断：Zone=512，状态=成功，主机代际=0/1，代际可信=1"))
        lines = [line.replace("Zone=512，状态=成功，主机代际=0/1", "Zone=512，状态=成功，主机代际=1/2")
                 if i==len(lines)-2 else line for i,line in enumerate(lines)]
        with self.assertRaisesRegex(ValueError,"上次同代目标"):
            self.parse(lines)

    def test_mismatched_source_rejected(self):
        with self.assertRaisesRegex(ValueError,"上次同代目标"):
            self.parse(seed()+batch(256,512,tail=16*MIB)+batch(1024,768,second=2))

    def test_missing_closure_is_unknown_not_assumed_finish(self):
        with self.assertRaisesRegex(ValueError,"封闭原因未知"):
            self.parse(seed()+batch(256,512)+batch(512,768,second=2))

    def test_recreated_name_rejected(self):
        with self.assertRaisesRegex(ValueError,"锚点重建"):
            self.parse(seed()+seed()+batch(256,512))

    def test_multiple_extents_rejected(self):
        with self.assertRaisesRegex(ValueError,"单片段"):
            self.parse([s.replace("extent count: 1","extent count: 2") for s in seed()+batch(256,512)])

    def test_unknown_normal_target_not_filled_in(self):
        with self.assertRaisesRegex(ValueError,"普通／重复目标"):
            self.parse([s for s in seed()+batch(256,512) if "迁移使用专用预留" not in s])

    def test_physical_size_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError,"物理申请"):
            self.parse([s.replace(f"需要={16*MIB}",f"需要={16*MIB+512}") for s in seed()+batch(256,512)])

    def test_missing_release_rejected(self):
        with self.assertRaisesRegex(ValueError,"结束记录不配对"):
            self.parse([s for s in seed()+batch(256,512) if "ReleaseMigrateZone" not in s])

    def test_truncated_copy_rejected(self):
        with self.assertRaisesRegex(ValueError,"截断"):
            self.parse((seed()+batch(256,512))[:-3])

    def test_wrong_reset_generation_rejected(self):
        with self.assertRaisesRegex(ValueError,"Reset代际"):
            self.parse(seed()+batch(256,512,generation=1))

    def test_stats_total_must_close(self):
        with self.assertRaisesRegex(ValueError,"维护字节"):
            self.parse([s.replace(f"本轮迁移 {16*MIB}","本轮迁移 1") for s in seed()+batch(256,512)])

    def test_measurement_uses_full_history(self):
        result=self.parse(self.natural())
        selected=measured(result["复制"],2,3)
        self.assertEqual(len(selected),1)
        self.assertNotIn("首次",summarize(selected))
        self.assertEqual(measured(result["复制"],3,4),[])

    def test_file_retirement_is_only_posthoc(self):
        result=self.parse(self.natural()+[log(f"DeleteFile: {NAME}.retired.bin",5)])
        self.assertEqual(result["复制"][-1]["距退役秒"],3)

    def test_paired_net_difference_not_repeat_total(self):
        a={"预热后复制":self.parse(self.natural())["复制"]}
        b=deepcopy(a)
        self.assertEqual(compare(a,b)["净迁移变化MiB"],0)
        b["预热后复制"].append(deepcopy(b["预热后复制"][-1]))
        result=compare(a,b)
        self.assertEqual(result["净迁移变化MiB"],16)
        self.assertEqual(result["预热后分项差MiB（加权减Greedy）"]["自然写满后ZC"],16)


if __name__ == "__main__":
    unittest.main()
