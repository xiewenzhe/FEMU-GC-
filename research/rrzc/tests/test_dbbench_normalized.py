"""归一化边界验证；合成数值仅为单元测试，不作实验结果。"""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from plot_dbbench_normalized import ratio, metric_rows, MAINTENANCE


class NormalizedTest(unittest.TestCase):
    def test_positive_base_is_one(self):
        self.assertEqual(ratio(17,17),1)
        self.assertEqual(ratio(80,100),.8)

    def test_throughput_not_inverted(self):
        self.assertEqual(ratio(120,100),1.2)

    def test_zero_and_missing_never_fabricated(self):
        self.assertIsNone(ratio(0,0))
        self.assertIsNone(ratio(8,0))
        self.assertIsNone(ratio(None,10))
        self.assertIsNone(ratio(10,None))
        self.assertEqual(ratio(0,10),0)

    def test_invalid_input_rejected(self):
        for value in (-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):ratio(value,10)
            with self.assertRaises(ValueError):ratio(10,value)

    def test_missing_write_and_zero_rr_remain_distinct(self):
        a={key:0 for key,_,_ in MAINTENANCE};b=dict(a)
        a['总迁移字节']=100;b['总迁移字节']=80
        a['操作延迟']={};b['操作延迟']={}
        rows=metric_rows([('hot',{'模式':{'space':a,'model':b}},None)])
        indexed={r['指标']:r for r in rows}
        self.assertEqual(indexed['总迁移字节']['模型归一化'],.8)
        self.assertEqual(indexed['总迁移字节']['基线归一化'],1)
        self.assertEqual(indexed['RR次数']['状态'],'两组均为0，比例不适用')
        self.assertEqual(indexed['WRITE-平均微秒']['状态'],'缺少该操作统计，不适用')
        self.assertIsNone(indexed['RR次数']['基线归一化'])


if __name__=='__main__':unittest.main()
