"""重复性分析拒绝配置混用；合成配置不作为实验结果。"""
import unittest
from pathlib import Path
from analyze_repro_repeats import same_conditions,analyze


class RepeatConditionsTest(unittest.TestCase):
    def plan(self):
        return dict(初始装载键数=917504,RC策略='matched',真实块计数准备=True,内部随机性控制=True,
            前台固定全局顺序=True,固定逻辑输入=True,共享单后台线程=True,每次写后排空后台=True,
            逐请求跟踪=True,设备稀疏事件=False,Flush同步分段=True,GC批次检查门=False,最大写缓冲数=2)

    def test_legacy_ext4_matches_explicit_default(self):
        a=self.plan();b=dict(a,辅助目录tmpfs诊断=False)
        same_conditions(a,b)

    def test_tmpfs_or_background_change_is_rejected(self):
        a=self.plan()
        for key,value in (('辅助目录tmpfs诊断',True),('共享单后台线程',False)):
            with self.assertRaisesRegex(ValueError,key):same_conditions(a,dict(a,**{key:value}))

    def test_missing_conditions_are_not_assumed_equal(self):
        with self.assertRaisesRegex(ValueError,'证据缺失'):same_conditions({}, {})

    def test_controlled_schedule_and_rounds_cannot_be_mixed(self):
        a=dict(self.plan(),目标分配稀疏诊断=True,实际业务批次数=24,受控空间请求确认=True)
        same_conditions(a,dict(a))
        for key,value in (('目标分配稀疏诊断',False),('实际业务批次数',16),('受控空间请求确认',False)):
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,key):
                same_conditions(a,dict(a,**{key:value}))

    def test_same_result_cannot_count_as_two_independent_rounds(self):
        with self.assertRaisesRegex(ValueError,'同一结果目录'):
            analyze(Path('/tmp/合成结果'),Path('/tmp/合成结果'),Path('/tmp/不应生成'))


if __name__=='__main__':unittest.main()
