"""退役删失、Reset代际和原始行号反例；合成数据不作实测结果。"""
import unittest
from analyze_flush_retirement import within_horizon,next_generation_task,resolve_task


class RetirementTest(unittest.TestCase):
    def test_censored_is_unknown_and_not_zero_lifetime(self):
        second=10**9
        self.assertIsNone(within_horizon(0,None,5*second,10))
        self.assertFalse(within_horizon(0,None,11*second,10))
        self.assertTrue(within_horizon(0,3*second,11*second,10))
        self.assertFalse(within_horizon(0,11*second,12*second,10))
        with self.assertRaises(ValueError):within_horizon(2,1,10,10)

    def test_same_zone_after_reset_is_not_same_generation_followup(self):
        tasks=[dict(原始开始行=20,Zone=7,源代际=1),dict(原始开始行=30,Zone=8,源代际=0)]
        self.assertIsNone(next_generation_task(tasks,10,7,0))
        self.assertEqual(next_generation_task(tasks,10,7,1),tasks[0])

    def test_wrapper_line_offset_is_verified_against_raw_fields(self):
        start='2026/10/30-00:00:00.000000 1 RR 开始：测试夹具'
        lines=[start,'2026/10/30-00:00:01.000000 1 V1后续迁移复制：文件编号=5，复制成功=1',
               '2026/10/30-00:00:02.000000 1 V1后续映射提交：文件编号=5，成功=1',
               '2026/10/30-00:00:03.000000 1 RR 统计：测试夹具']
        task=dict(类型='RR',原始选择行=start,开始行=2,结束行=5,
            复制=[dict(行=3,字段={'文件编号':'5','复制成功':'1'})],
            提交=[dict(行=4,字段={'文件编号':'5','成功':'1'})])
        fixed=resolve_task(lines,task)
        self.assertEqual(fixed['复制'][0]['原始行'],2)
        self.assertEqual(fixed['原始结束行'],4)
        task['复制'][0]['字段']['文件编号']='错误文件'
        with self.assertRaises(ValueError):resolve_task(lines,task)


if __name__=='__main__':unittest.main()
