"""手动入口与资源门禁回归；不启动QEMU、不修改用户镜像。"""
import json
import io
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finerr_profile import load
from manual_zns import check_memory, command, PROJECT
import manual_zns


class ManualZnsTest(unittest.TestCase):
    def test_command_uses_single_profile_and_keeps_os_image(self):
        argv = command(load(), Path('/home/data/xwz/femu.qcow2'), Path('/tmp/本轮'))
        self.assertTrue(any(x.startswith('femu,devsz_mb=32768,') for x in argv))
        self.assertIn('4G', argv)
        self.assertTrue(any('file=/home/data/xwz/femu.qcow2,' in x for x in argv))
        self.assertNotIn('sudo', argv)

    def test_dry_run_from_unrelated_directory_does_not_boot(self):
        for script in (PROJECT/'build-femu/run-zns.sh', PROJECT/'femu-scripts/run-zns.sh',
                       PROJECT/'hw/femu/scripts/run-zns.sh'):
            p = subprocess.run(['bash', str(script), '--dry-run'], cwd='/tmp',
                               text=True, capture_output=True, check=True)
            result = json.loads(p.stdout)
            self.assertIn('zns_page_size=4096', result['设备参数'])
            self.assertIn('zns_rr_threshold=1024', result['设备参数'])
            self.assertIn('zns_max_open=8', result['设备参数'])
            self.assertIn('zns_max_active=12', result['设备参数'])
            self.assertEqual(result['公共ZenFS环境']['ZENFS_RRZC_RECLAIM_BYTES'], '0')

    def test_resource_guard_does_not_silently_allow_unpinned_or_low_memory(self):
        p = load()
        check_memory(p, 64 << 30, 32 << 30)
        check_memory(p, 64 << 30, resource.RLIM_INFINITY)
        with self.assertRaisesRegex(ValueError, '内存不足'):
            check_memory(p, 48 << 30, 32 << 30)
        with self.assertRaisesRegex(ValueError, '锁页'):
            check_memory(p, 64 << 30, 16 << 30)

    def test_normal_child_archives_exit_and_clears_live_pid(self):
        # 用短命Python子进程验证启动器收尾，不把它冒充QEMU或设备实验。
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(manual_zns, 'preflight'), \
                mock.patch.object(manual_zns, 'command', return_value=
                                  [sys.executable, '-c', "print('测试正常退出')"]), \
                mock.patch.object(manual_zns.sys, 'stdout', mock.Mock(buffer=io.BytesIO())):
            code = manual_zns.launch(load(), Path('/不访问的测试镜像'), Path(directory))
            run = next(Path(directory).iterdir())
            state = json.loads((run/'状态.json').read_text())
            self.assertEqual(code, 0)
            self.assertEqual(state['阶段'], '已退出')
            self.assertIsNone(state['客机PID'])
            self.assertEqual(state['客机退出码'], 0)
            self.assertIn('测试正常退出', (run/'运行.log').read_text())

    def test_launch_failure_still_archives_without_fake_live_pid(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(manual_zns, 'preflight'), \
                mock.patch.object(manual_zns, 'command', return_value=['/不存在的测试程序']), \
                mock.patch.object(manual_zns.sys, 'stdout', mock.Mock(buffer=io.BytesIO())):
            with self.assertRaises(FileNotFoundError):
                manual_zns.launch(load(), Path('/不访问的测试镜像'), Path(directory))
            state = json.loads(next(Path(directory).iterdir()).joinpath('状态.json').read_text())
            self.assertEqual(state['启动器退出码'], 1)
            self.assertIsNone(state['客机PID'])


if __name__ == '__main__':
    unittest.main()
