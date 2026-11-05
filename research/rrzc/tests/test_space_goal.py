"""空间债务诊断的边界测试；合成数字不代表真实实验收益。"""
from pathlib import Path
import subprocess
import unittest

from space_goal_source import wrapper,smoke
from analyze_space_goal import verdict,GOAL

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('/home/data/xwz/rrzc-baselines/joint-controlled-unit-cost-v1-20261028-results/源码/run_calibrated_guest.sh')


class SpaceGoalTest(unittest.TestCase):
    def test_real_wrapper_keeps_business_and_restores_verify_policy(self):
        if not SOURCE.exists():self.skipTest('缺少归档包装器')
        old=SOURCE.read_text();new=wrapper(old)
        subprocess.run(['bash','-n'],input=new,text=True,check=True)
        prefix=new.split('seal_log run;snapshot business',1)[0]
        prefix=prefix.replace('experiment_program="$target/db_bench"\nexperiment_args=("${common[@]}")\n','',1)
        prefix=prefix.replace('"$experiment_program" "${experiment_args[@]}" "$@"',
                              '"$target/db_bench" "${common[@]}" "$@"',1)
        self.assertEqual(prefix,old.split('seal_log run;snapshot business',1)[0])
        self.assertIn('saved_greedy_execute=$cal_greedy_execute',new)
        self.assertIn('gc_gate=$saved_gc_gate;cal_greedy_execute=$saved_greedy_execute',new)
        self.assertLess(new.index('snapshot business'),new.index('phase goal'))
        self.assertLess(new.index('snapshot goal'),new.index('hotspot=0;phase verify'))
        with self.assertRaises(ValueError):wrapper(new)
        mini=smoke(new)
        self.assertNotIn('logical_input_guest.py" replay',mini)
        self.assertNotIn('reset-zone',mini)
        subprocess.run(['bash','-n'],input=mini,text=True,check=True)

    def test_less_copy_with_less_space_is_not_a_positive_verdict(self):
        a=dict(终点空闲字节=GOAL+100,终点有效字节=500,业务加收尾迁移字节=100)
        b=dict(终点空闲字节=GOAL,终点有效字节=500,业务加收尾迁移字节=90)
        self.assertTrue(verdict(a,b).startswith('未证明'))
        b['终点空闲字节']=GOAL+100
        self.assertTrue(verdict(a,b).startswith('本次受控路径少复制'))

    def test_missing_space_goal_or_different_valid_data_is_rejected(self):
        a=dict(终点空闲字节=GOAL,终点有效字节=500,业务加收尾迁移字节=100)
        for b in (dict(a,终点空闲字节=GOAL-1),dict(a,终点有效字节=499)):
            with self.assertRaises(AssertionError):verdict(a,b)

    def test_device_uses_native_check_and_has_bounded_exit(self):
        text=(ROOT/'tests/space_goal_device.cc').read_text()
        self.assertIn('rrzc_front_order::GcTick(gate,sequence)',text)
        self.assertNotIn('MigrateExtents(',text)
        self.assertNotIn('Reset(',text)
        self.assertIn('sequence>32',text)
        self.assertIn('stalled>=2',text)
        self.assertIn('logger->SetInfoLogLevel(DEBUG_LEVEL)',text)

    def test_real_replay_smoke_runs_after_auxiliary_mount(self):
        text=(ROOT/'tests/run_calibration_host.py').read_text()
        self.assertLess(text.index('aux_tmpfs_guest.py setup'),
                        text.index("wait_stage(run,'goal-smoke'"))
        self.assertIn('空间工具小测维护日志缺失，不运行完整业务',text)
        self.assertLess(text.index("goal_arm(admission/tag)"),text.index("goal_guard=goal_repeat"))


if __name__=='__main__':unittest.main()
