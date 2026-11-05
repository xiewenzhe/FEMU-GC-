"""仅模拟监督器收尾分支，不启动、终止或读取任何真实客机。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import run_benefit_device_host as host


class DeviceCleanupTest(unittest.TestCase):
    def runner(self):
        run=object.__new__(host.DeviceRun)
        run.state={'当前客机PID':123456789};run.record=Mock()
        return run

    def test_normal_shutdown_needs_no_extra_wait(self):
        run=self.runner()
        with patch.object(host.TargetRun,'shutdown',return_value=None) as close,patch.object(host.time,'sleep') as sleep:
            run.shutdown();close.assert_called_once();sleep.assert_not_called()

    def test_connection_close_then_pid_exit_is_success(self):
        run=self.runner()
        with patch.object(host.TargetRun,'shutdown',side_effect=EOFError),\
             patch.object(host.Path,'exists',side_effect=[True,False]),patch.object(host.time,'sleep') as sleep:
            run.shutdown();sleep.assert_called_once_with(1)
        self.assertIn('原客机PID随后已退出',run.record.call_args.kwargs['关机反馈'])

    def test_live_pid_after_connection_error_is_not_success(self):
        run=self.runner()
        with patch.object(host.TargetRun,'shutdown',side_effect=EOFError),\
             patch.object(host.Path,'exists',return_value=True),patch.object(host.time,'sleep') as sleep:
            with self.assertRaises(RuntimeError):run.shutdown()
            self.assertEqual(sleep.call_count,60)
        run.record.assert_not_called()

    def test_archive_failure_still_closes_but_does_not_pass(self):
        run=self.runner();run.collect_target=Mock(side_effect=RuntimeError('合成归档错误'))
        run.shutdown=Mock()
        with tempfile.TemporaryDirectory(prefix='rrzc-cleanup-unit-') as directory:
            with patch.object(host.Path,'exists',return_value=True):
                with self.assertRaises(RuntimeError):run.finish_stage(Path(directory),'合成')
            result=json.loads((Path(directory)/'阶段归档.json').read_text())
            self.assertFalse(result['完整']);self.assertIn('合成归档错误',result['错误'][0])
        run.shutdown.assert_called_once()


if __name__=='__main__':unittest.main()
