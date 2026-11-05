"""整数候选资格和逐线程迁移关联反例；合成日志只用于解析测试。"""
import unittest
from analyze_sst_placement_limits import original_qualification, allocation_evidence


class LimitsTest(unittest.TestCase):
    def test_strict_integer_gate(self):
        self.assertEqual(original_qualification(17033616,128<<20,88), dict(无效比例整数近似=88,资格=False))
        self.assertTrue(original_qualification(17033616,128<<20,85)['资格'])
        self.assertFalse(original_qualification(0,128<<20,85)['资格'])
        with self.assertRaises(ValueError): original_qualification(2,1,85)

    def test_allocation_ignores_other_threads_and_prior_file(self):
        lines = [
            'time 7 RR 开始：Zone 0',
            'time 7 MigrateFileExtents, fname: /H.sst, extent count: 1',
            'time 8 协同池回退：类别=1，原因=其他线程原因',
            'time 7 协同池回退：类别=1，原因=无普通Active令牌',
            'time 7 V1后续迁移复制：',
            'time 7 MigrateFileExtents, fname: /N.sst, extent count: 1',
            'time 7 V1后续迁移复制：']
        def copy(name, line):
            return dict(行=line+1, 字段=dict(文件名=name,源Zone='0',目标Zone=str(128<<20),目标片段=str(128<<20),有效字节='4096'))
        task=dict(类型='RR',原始选择行=lines[0],开始行=2,复制=[copy('/H.sst',5),copy('/N.sst',7)])
        result=allocation_evidence(lines,task,{'/H.sst':'H'})
        self.assertEqual(result[0]['原因'],'无普通Active令牌')
        self.assertEqual(result[1]['分配路径'],'未分类普通分配')
        with self.assertRaises(ValueError): allocation_evidence(lines,task,{'/H.sst':'H','/N.sst':'W'})


if __name__=='__main__': unittest.main()
