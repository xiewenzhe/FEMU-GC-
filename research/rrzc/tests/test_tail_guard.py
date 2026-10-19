"""合成尾部约束回归，示例数字不是设备实验结果。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from replay_tail_guard import guard,candidate_bounds,CAPACITY

MIB=1<<20


def candidate(*lengths):
    return {"有效字节":str(sum(lengths)),"片段":[{"次序":str(i),"逻辑字节":str(n)} for i,n in enumerate(lengths,1)]}


def reserve(tail=16*MIB,valid=240*MIB):
    return {"可知":"1","存在":"1","剩余字节":str(tail),"有效字节":str(valid)}


class TailGuard(unittest.TestCase):
    def test_all_proposed_fragments_fit(self):
        result=guard(candidate(16*MIB),candidate(32*MIB),reserve(64*MIB,192*MIB),512)
        self.assertTrue(result["保留原建议"])

    def test_exact_greedy_boundary_falls_back_unknown(self):
        result=guard(candidate(16*MIB),candidate(32*MIB),reserve(),512)
        self.assertEqual(result["分类"],"未知回退")
        self.assertEqual(result["条件首片段丢弃范围字节"]["相对增加范围"],[0,16*MIB])
        self.assertFalse(result["已确认实际Finish"])

    def test_clear_relative_reserve_pressure(self):
        result=guard(candidate(15*MIB),candidate(32*MIB),reserve(),512)
        self.assertEqual(result["分类"],"预留压力保守拦截")
        self.assertFalse(result["保留原建议"])
        self.assertFalse(result["已证明额外搬运"])

    def test_known_alternative_target_avoids_veto(self):
        result=guard(candidate(15*MIB),candidate(32*MIB),reserve(),512,True)
        self.assertTrue(result["保留原建议"])

    def test_unknown_alternative_does_not_prove_finish(self):
        result=guard(candidate(15*MIB),candidate(32*MIB),reserve(),512,None)
        self.assertFalse(result["已确认实际Finish"])
        self.assertIsNone(result["其他普通目标可完整容纳"])

    def test_known_no_alternative_still_not_future_migration(self):
        result=guard(candidate(15*MIB),candidate(32*MIB),reserve(),512,False)
        self.assertFalse(result["已证明额外搬运"])

    def test_no_live_data_reset_branch(self):
        result=guard(candidate(16*MIB),candidate(32*MIB),reserve(16*MIB,0),512)
        self.assertTrue(result["保留原建议"])
        self.assertNotIn("条件首片段丢弃范围字节",result)

    def test_no_reserve_unknown(self):
        r=reserve(); r["存在"]="0"
        self.assertEqual(guard(candidate(16*MIB),candidate(32*MIB),r,512)["分类"],"未知回退")

    def test_unknown_reserve(self):
        r=reserve(); r["可知"]="0"
        self.assertFalse(guard(candidate(16*MIB),candidate(32*MIB),r,512)["保留原建议"])

    def test_both_first_fragments_do_not_fit(self):
        result=guard(candidate(32*MIB),candidate(48*MIB),reserve(),512)
        self.assertEqual(result["分类"],"未知回退")
        self.assertEqual(result["条件首片段丢弃范围字节"]["相对增加范围"],[0,0])

    def test_only_first_fragment_fits_not_enough(self):
        result=guard(candidate(8*MIB),candidate(8*MIB,16*MIB),reserve(),512)
        self.assertFalse(result["保留原建议"])

    def test_multiple_fragments_all_fit(self):
        result=guard(candidate(8*MIB),candidate(4*MIB,8*MIB),reserve(),512)
        self.assertTrue(result["保留原建议"])

    def test_proposed_sparse_boundary_unknown(self):
        result=guard(candidate(8*MIB),candidate(16*MIB),reserve(),512)
        self.assertFalse(result["保留原建议"])

    def test_missing_fragments_unknown(self):
        result=guard(candidate(8*MIB),{"有效字节":"10","片段":[]},reserve(),512)
        self.assertFalse(result["保留原建议"])

    def test_coverage_mismatch_unknown(self):
        c=candidate(32*MIB); c["有效字节"]="1"
        self.assertIsNone(candidate_bounds(c,512))

    def test_bad_order_rejected(self):
        c=candidate(32*MIB); c["片段"][0]["次序"]="2"
        with self.assertRaises(ValueError): candidate_bounds(c,512)

    def test_oversized_physical_fragment_unknown(self):
        self.assertIsNone(candidate_bounds(candidate(CAPACITY),512))

    def test_invalid_alignment_unknown(self):
        self.assertIsNone(candidate_bounds(candidate(16*MIB),0))

    def test_invalid_capacity_rejected(self):
        with self.assertRaises(ValueError): guard(candidate(8*MIB),candidate(16*MIB),reserve(32*MIB,240*MIB),512)

    def test_invalid_alternative_flag_rejected(self):
        with self.assertRaises(ValueError): guard(candidate(8*MIB),candidate(16*MIB),reserve(),512,"未知")

    def test_future_retirement_and_actual_dense_copy_not_used(self):
        g,c=candidate(16*MIB),candidate(32*MIB); r=reserve()
        before=guard(g,c,r,512)
        g["事后实际物理申请量"]=16*MIB; c["后续文件退役秒数"]=1; c["实际Finish"]=True
        self.assertEqual(guard(g,c,r,512),before)


if __name__=="__main__": unittest.main()
