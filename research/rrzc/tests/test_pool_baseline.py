"""新配置基线的隔离约束测试，替身输入不作为实验结果。"""
from types import SimpleNamespace
from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import Mock,patch
from run_calibration_host import validate_fresh_mode, business_modes, verify_device_limits
from metadata_start_guest import align


class PoolBaselineTest(unittest.TestCase):
    def args(self,**changes):
        values=dict(baseline_only=True,fresh_image=True,reuse_client='joint-controlled-source',
            matched_observation=True,model_pair=False,unit_cost=False,reclaim_price=False,
            space_goal=False,repeat_acceptance=None,cleanup_archived=None,only_prepare=False)
        values.update(changes)
        return SimpleNamespace(**values)

    def test_new_baseline_runs_only_two_greedy_arms(self):
        validate_fresh_mode(self.args())
        self.assertEqual(business_modes(False), (('g1','greedy'),('g2','greedy')))

    def test_cannot_bypass_existing_model_admission(self):
        for change in (dict(model_pair=True),dict(unit_cost=True),dict(space_goal=True),
                       dict(repeat_acceptance='旧0/6准入'),dict(fresh_image=False),
                       dict(matched_observation=False),dict(only_prepare=True)):
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_fresh_mode(self.args(**change))

    def test_old_fresh_model_still_requires_acceptance(self):
        with self.assertRaises(ValueError):
            validate_fresh_mode(self.args(baseline_only=False,model_pair=True))
        validate_fresh_mode(self.args(baseline_only=False,model_pair=True,repeat_acceptance='验收'))

    def test_pty_connection_notice_does_not_enter_json(self):
        with tempfile.TemporaryDirectory() as directory:
            run=SimpleNamespace(target='/home/femu/joint-test',output=Path(directory),
                profile={'沿用条件':{'最大活动Zone数':12,'最大打开Zone数':8}},
                root=Mock(return_value='连接关闭，不是JSON'),
                ssh=Mock(return_value='{"mar":11,"mor":7}'))
            self.assertEqual(verify_device_limits(run)['最大活动Zone数'],12)
            self.assertIn('> /home/femu/joint-test/设备资源查询.json',run.root.call_args.args[0])
            run.ssh.return_value='{"mar":5,"mor":7}'
            with self.assertRaises(ValueError):verify_device_limits(run)

    def test_new_metadata_reference_is_observed_and_never_reduces_rc(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);(base/'启用本轮元数据参考').write_text('新配置')
            for tag in ('g1','g2'):(base/tag).mkdir()
            def query(z):return [dict(Zone=z,块=0,Channel=0,LUN=0,Plane=0,物理块=z,RC=4)]
            counter=dict(实际NAND读取=0,阈值触发事件=0)
            with self.assertRaises(AssertionError):align(base/'g2',query,Mock(),lambda:counter)
            read_page=Mock()
            with patch('metadata_start_guest.os.open',return_value=123),patch('metadata_start_guest.os.close'):
                align(base/'g1',query,read_page,lambda:counter)
            self.assertEqual(json.loads((base/'元数据起点目标.json').read_text())[0]['RC'],4)
            read_page.assert_not_called()
            def higher(z):return [dict(query(z)[0],RC=5)]
            with self.assertRaises(AssertionError):align(base/'g2',higher,read_page,lambda:counter)
            self.assertTrue((base/'g2/元数据参考比较.json').exists())


if __name__=='__main__':unittest.main()
