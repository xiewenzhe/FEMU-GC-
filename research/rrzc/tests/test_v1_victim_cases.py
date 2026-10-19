"""核验受控负载定义；计划数据不是实际实验结果。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_stream import expected_reads
from analyze_v1_victim_value import event_kind


class VictimCases(unittest.TestCase):
    def test_original_unchanged(self):
        self.assertEqual(expected_reads(180),expected_reads(180,"original"))
        self.assertEqual(len(expected_reads(180)),51888)

    def test_only_stop_hot_requests(self):
        original=expected_reads(180)
        cases=expected_reads(180,"maintenance-cases")
        self.assertEqual(len(cases),50384)
        self.assertEqual([r for r in cases if r[2]==0],[r for r in original if r[2]==0])
        self.assertEqual([r for r in cases if r[1]%3==0],[r for r in original if r[1]%3==0])

    def test_age_65_cutoff(self):
        cases=expected_reads(180,"maintenance-cases")
        for g in (1,2):
            reads=[r for r in cases if r[1]==g and r[2]==1]
            self.assertTrue(reads)
            self.assertLess(max(r[0] for r in reads),(30*g+65)*10**9)
            self.assertEqual(len(reads),3916)

    def test_maintenance_is_not_prediction_gain(self):
        self.assertIn("独立RR",event_kind([(10,"RR",""),(12,"ZC","")]))
        self.assertIn("受干预",event_kind([(10,"ZC",""),(12,"RR","")]))
        self.assertIn("先退役",event_kind([(10,"退役",""),(12,"RR","")]))
        self.assertIn("未知",event_kind([]))


if __name__=="__main__":
    unittest.main()
