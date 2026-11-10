"""Protect credential handling and the Unix control-socket length boundary."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / 'common'))
import reliable_startup as startup
import run_comparison_host as controller


class StartupTest(unittest.TestCase):
    def test_printed_survival_socket_boundary(self):
        prefix = '/tmp/joint-related-compare-bootstrap-coldmap-v1-20261110-'
        printed = prefix + 'cold-printed-calibration-ssh.sock'
        survival = prefix + 'cold-survival-calibration-ssh.sock'
        self.assertEqual(len(printed) + 17, 107)
        self.assertEqual(len(survival) + 17, 108)
        self.assertEqual(startup.control_path(printed), printed)
        self.assertNotEqual(startup.control_path(survival), survival)
        self.assertLess(len(startup.control_path(survival)) + 17, 108)

    def test_master_success_needs_independent_identity_and_no_secret_recording(self):
        with tempfile.TemporaryDirectory() as temp:
            run = SimpleNamespace(socket=temp + '/socket', output=Path(temp), port='8101',
                                  password='test-secret', record=mock.Mock())
            child = mock.Mock(exitstatus=0, signalstatus=None, before='')
            child.expect.side_effect = [0, 0]; child.isalive.return_value = False
            reply = SimpleNamespace(returncode=0, stdout='uid=1000(femu) gid=1000(femu)\n')
            with mock.patch.object(startup.pexpect, 'spawn', return_value=child) as spawn, \
                 mock.patch.object(startup.subprocess, 'run', return_value=reply) as ssh:
                startup.authenticate(run)
            self.assertTrue(spawn.call_args.kwargs['echo'] is False)
            self.assertIn('-N', spawn.call_args.args[1])
            self.assertIn('BatchMode=yes', ssh.call_args.args[0])
            self.assertEqual(ssh.call_args.args[0][-1], 'id')
            result = (Path(temp) / '启动认证核验.json').read_text()
            self.assertNotIn(run.password, result)
            self.assertTrue(json.loads(result)['passed'])

    def test_master_failure_does_not_retry_password_or_run_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            run = SimpleNamespace(socket=temp + '/socket', output=Path(temp), port='8101',
                                  password='test-secret', record=mock.Mock())
            child = mock.Mock(exitstatus=255, signalstatus=None,
                              before='test-secret Permission denied')
            child.expect.side_effect = [0, 0]; child.isalive.return_value = False
            with mock.patch.object(startup.pexpect, 'spawn', return_value=child) as spawn, \
                 mock.patch.object(startup.subprocess, 'run') as ssh:
                with self.assertRaisesRegex(AssertionError, 'master failed'): startup.authenticate(run)
            self.assertEqual(spawn.call_count, 1); ssh.assert_not_called()
            result = (Path(temp) / '启动认证核验.json').read_text()
            self.assertNotIn(run.password, result)
            self.assertEqual(json.loads(result)['attempts'][0]['diagnostics'], ['Permission denied'])

    def test_legacy_socket_diagnostic_is_separate_from_authentication(self):
        with tempfile.TemporaryDirectory() as temp:
            legacy = '/tmp/' + 'x' * 86
            run = SimpleNamespace(socket=temp + '/socket', output=Path(temp), port='8101',
                password='test-secret', record=mock.Mock(), diagnose_legacy_control_path=legacy)
            master = mock.Mock(exitstatus=0, signalstatus=None, before='')
            probe = mock.Mock(exitstatus=255, signalstatus=None, before='unix_listener: path too long')
            for child in (master, probe):
                child.expect.side_effect = [0, 0]; child.isalive.return_value = False
            reply = SimpleNamespace(returncode=0, stdout='uid=1000(femu)\n')
            with mock.patch.object(startup.pexpect, 'spawn', side_effect=[master, probe]), \
                 mock.patch.object(startup.subprocess, 'run', return_value=reply):
                startup.authenticate(run)
            receipt = (Path(temp) / '启动认证核验.json').read_text()
            self.assertNotIn(run.password, receipt)
            result = json.loads(receipt)
            self.assertTrue(result['passed'])
            self.assertTrue(result['legacy_socket_probe']['reproduced'])

    def test_predeployment_failure_preserves_original_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); payload = root / 'payload'; payload.write_text('input')
            run = mock.Mock(state={'结果保存策略': {}, '镜像处理': {}}, archive_exclusions=())
            run.start.side_effect = RuntimeError('original startup failure')
            args = SimpleNamespace(label='case', build=root, windows=12)
            with mock.patch.object(controller, 'wait_slot'), \
                 mock.patch.object(controller, 'device_payload', return_value=(payload, {'client_files': {}})), \
                 mock.patch.object(controller, 'CalibrationRun', return_value=run), \
                 mock.patch.object(controller, 'configure'), mock.patch.object(controller, 'digest', return_value='sha'), \
                 mock.patch.object(controller, 'compact') as compact:
                with self.assertRaisesRegex(RuntimeError, 'original startup failure'):
                    controller.run_arm(args, root, None, 'arm', 'coldmap', 'artifact-survival')
            run.finish.assert_called_once(); compact.assert_not_called()
            self.assertTrue(payload.exists())


if __name__ == '__main__': unittest.main()
