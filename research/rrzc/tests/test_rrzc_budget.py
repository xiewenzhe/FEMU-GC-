"""净成本预算边界测试：合成输入不代表设备节省。"""
from fractions import Fraction
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_rrzc_budget import compare_cost


class BudgetCostTest(unittest.TestCase):
    def test_minimum_credit_includes_reclaim_penalty(self):
        out=compare_cost(64,32,256,0)
        needed=out['严格胜过Greedy所需最小抵扣字节']
        self.assertGreater(needed,out['额外迁移字节'])
        self.assertFalse(compare_cost(64,32,256,needed-1)['单位回收净成本更低'])
        self.assertTrue(compare_cost(64,32,256,needed)['单位回收净成本更低'])

    def test_exact_fraction_boundaries(self):
        for capacity in (32,64,256):
            for greedy in range(1,capacity//4):
                for valid in (greedy,min(capacity-1,greedy+8)):
                    out=compare_cost(valid,greedy,capacity,0)
                    needed=out['严格胜过Greedy所需最小抵扣字节']
                    for credit in (0,max(0,needed-1),needed,valid):
                        expected=Fraction(valid-credit,capacity-valid)<Fraction(greedy,capacity-greedy)
                        self.assertEqual(compare_cost(valid,greedy,capacity,credit)['单位回收净成本更低'],expected)

    def test_bad_inputs(self):
        for args in ((0,0,256,0),(30,31,256,0),(256,32,256,0),(64,32,256,65),(64,32,256,-1)):
            with self.assertRaises(ValueError):compare_cost(*args)


if __name__=='__main__':unittest.main()
