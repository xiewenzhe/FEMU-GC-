"""合成输入只验证替换前快照及未知边界，不是收益实验。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_reserve_replacements import inspect, local_fit, eligible, CAPACITY, MIB
from analyze_long_repeats import parse_ledger
from test_long_repeats import log, batch, ORIGIN


def simple(zone,size):
    return (f"简单双因素候选：Zone={zone}，有效字节={size}，容量字节={CAPACITY}，"
            "最大块读数=4000，读数阈值=4096，覆盖资格=1，空目标资格=1，读压力可用=1")


def create(name,zone,second=0):
    return [log(f"New writable file: {name} direct: 1",second),
            log(f"Allocating zone(new=1) start: 0x{zone:x} wp: 0x{zone:x}",second)]


def fixture():
    # 固定写入240MiB迁移载荷，正常退役其中192MiB，得到48MiB有效和16MiB尾部。
    pool = 3*CAPACITY
    lines = [log(f"迁移预留取得空区：Zone={pool}")]
    for i in range(15):
        name = f"/rrzc-stream/g{i}.cold.anchor.sst"
        source = (4+i)*CAPACITY
        lines += create(name,source,i+1)
        lines += [log(simple(source,16*MIB),i+1)]
        lines += batch(source,pool,tail=(256-16*i)*MIB,second=i+1,name=name)
    for i in range(12):
        name = f"/rrzc-stream/g{i}.cold.anchor.sst"
        lines += [log(f"Rename file: {name} to : {name}.retired.bin",17),
                  log(f"DeleteFile: {name}.retired.bin",17)]
    cold,hot = "/rrzc-stream/g20.cold.anchor.sst","/rrzc-stream/g20.hot.anchor.sst"
    g,c,new = 24*CAPACITY,25*CAPACITY,26*CAPACITY
    lines += create(cold,g,18)+create(hot,c,18)
    lines += [log(simple(g,16*MIB),19),log(simple(c,32*MIB),19)]
    lines += batch(c,new,second=19,name=hot,before=(f"迁移预留替换：旧Zone={pool}，新Zone={new}",))
    return lines


def candidate(*sizes):
    return {"片段":[{"逻辑字节":s*MIB} for s in sizes]}


class ReserveReplacementsTests(unittest.TestCase):
    def run_fixture(self,lines=None,ledger=None):
        lines = fixture() if lines is None else lines
        ledger = parse_ledger(lines,ORIGIN) if ledger is None else ledger
        return inspect(lines,ledger,ORIGIN,512)

    def test_same_snapshot_exposes_upstream_cost(self):
        out = self.run_fixture()
        self.assertEqual(out["提前替换数"],1)
        r = out["替换"][0]
        self.assertEqual(r["分类"],"Greedy全部可装而改选不能全部装入")
        self.assertEqual(r["当前预留"]["有效字节"],str(48*MIB))
        self.assertEqual(r["当前预留"]["剩余字节"],str(16*MIB))
        self.assertTrue(r["相对Greedy改选"])
        self.assertTrue(r["选择尾部与替换尾部一致"])

    def test_dense_fact_does_not_shrink_production_unknown_bounds(self):
        r = self.run_fixture()["替换"][0]["当前护栏物理上下界诊断"]
        self.assertEqual(r["分类"],"未知回退")
        self.assertEqual(r["Greedy物理界限"]["首上界"],16*MIB+512)
        self.assertFalse(r["已证明额外搬运"])

    def test_history_is_before_choice(self):
        r = self.run_fixture()["替换"][0]
        for label in ("Greedy","实际候选"):
            for p in r[label]["片段"]:
                self.assertLess(p["依据行号"],r["选择行号"])
        self.assertLess(r["当前预留"]["历史依据行号"],r["选择行号"])

    def test_missing_greedy_mapping_stays_unknown(self):
        lines = fixture()
        target = f"start: 0x{24*CAPACITY:x}"
        lines = [s for s in lines if target not in s]
        r = self.run_fixture(lines)["替换"][0]
        self.assertFalse(r["Greedy"]["片段可知"])
        self.assertEqual(r["分类"],"信息不足")

    def test_missing_actual_mapping_is_not_filled_from_future_copy(self):
        lines = [s for s in fixture() if f"start: 0x{25*CAPACITY:x}" not in s]
        with self.assertRaisesRegex(ValueError,"过去布局"):
            self.run_fixture(lines)

    def test_snapshot_coverage_mismatch_is_unknown(self):
        lines = [s.replace(f"Zone={24*CAPACITY}，有效字节={16*MIB}",
                           f"Zone={24*CAPACITY}，有效字节={15*MIB}") for s in fixture()]
        self.assertEqual(self.run_fixture(lines)["替换"][0]["分类"],"信息不足")

    def test_future_labels_cannot_change_classification(self):
        lines = fixture()
        ledger = parse_ledger(lines,ORIGIN)
        before = self.run_fixture(lines,ledger)["替换"][0]
        altered = deepcopy(ledger)
        for c in altered["复制"]:
            c["退役"] = {"行号":999999,"经过秒":999999}
            c["距退役秒"] = 0
        after = self.run_fixture(lines,altered)["替换"][0]
        for k in ("分类","Greedy","实际候选","当前预留","当前护栏物理上下界诊断"):
            self.assertEqual(before[k],after[k])

    def test_replacement_without_later_repeat_is_not_saving(self):
        r = self.run_fixture()["替换"][0]
        self.assertEqual(r["事后同代源重复复制"],[])
        self.assertEqual(r["分类"],"Greedy全部可装而改选不能全部装入")

    def test_changed_replacement_tail_is_not_same_snapshot(self):
        lines = fixture()
        ledger = parse_ledger(lines,ORIGIN)
        ledger["封闭"][-1]["尾部字节"] = 8*MIB
        r = self.run_fixture(lines,ledger)["替换"][0]
        self.assertFalse(r["选择尾部与替换尾部一致"])
        self.assertEqual(r["分类"],"选择后状态改变或未知")

    def test_non_direct_anchor_rejected(self):
        with self.assertRaisesRegex(ValueError,"直接写"):
            self.run_fixture([s.replace("direct: 1","direct: 0") for s in fixture()])

    def test_multifragment_total_not_only_first(self):
        self.assertEqual(local_fit(candidate(16),candidate(16,32),{"剩余字节":str(16*MIB)}),
                         "Greedy全部可装而改选不能全部装入")

    def test_both_do_not_fit(self):
        self.assertEqual(local_fit(candidate(16),candidate(32),{"剩余字节":str(8*MIB)}),
                         "两者均不能全部装入")

    def test_proposed_fits(self):
        self.assertEqual(local_fit(candidate(16),candidate(32),{"剩余字节":str(32*MIB)}),"改选全部可装")

    def test_unknown_fragments_and_reserve(self):
        for g,c,r in ((None,candidate(32),{"剩余字节":"0"}),
                      (candidate(),candidate(32),{"剩余字节":"0"}),
                      (candidate(16),candidate(32),None)):
            self.assertEqual(local_fit(g,c,r),"信息不足")

    def test_original_budget_and_eligibility(self):
        c={"有效字节":64*MIB,"容量字节":CAPACITY,"覆盖资格":1,"空目标资格":1}
        self.assertTrue(eligible(c))
        self.assertFalse(eligible(dict(c,有效字节=64*MIB+1)))
        self.assertFalse(eligible(dict(c,覆盖资格=0)))
        self.assertFalse(eligible(dict(c,空目标资格=0)))


if __name__ == "__main__":
    unittest.main()
