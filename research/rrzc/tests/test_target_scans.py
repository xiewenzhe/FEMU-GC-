"""合成日志只验证目标扫描解释，不能作为实测结果。"""
import unittest
from analyze_target_scans import parse


class TargetScansTest(unittest.TestCase):
    def test_busy_target_is_not_assumed_to_have_zero_capacity(self):
        line='时点 7 迁移目标扫描：最小容量=10，文件寿命=4，匹配Zone=100，最佳差=50，候选= '+\
            '[Zone=100，取得=1，WP=100，剩余=20，有效=8，满=0，类别=1，寿命=4，匹配差=50] '+\
            '[Zone=200，取得=0]'
        result=parse(line)
        self.assertTrue(result['与日志匹配目标一致'])
        self.assertNotIn('剩余',result['候选'][1])
        self.assertIn('Acquire失败',result['候选'][1]['资格解释'])

    def test_later_equal_match_wins_without_changing_rule(self):
        line='时点 7 迁移目标扫描：最小容量=10，文件寿命=4，匹配Zone=200，最佳差=50，候选= '+\
            '[Zone=100，取得=1，WP=100，剩余=20，有效=8，满=0，类别=1，寿命=4，匹配差=50] '+\
            '[Zone=200，取得=1，WP=200，剩余=20，有效=8，满=0，类别=1，寿命=4，匹配差=50]'
        self.assertEqual(parse(line)['按采样字段重放目标'],200)


if __name__=='__main__':unittest.main()
