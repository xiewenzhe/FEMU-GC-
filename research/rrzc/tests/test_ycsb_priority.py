"""优先队列与安全接班的合成测试；不启动或停止真实进程。"""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tests'))
import run_ycsb_priority_host as queue


class PriorityTest(unittest.TestCase):
    def test_workload_order_does_not_alter_profiles(self):
        self.assertEqual(queue.ORDER, ('f', 'b', 'e'))
        self.assertEqual(queue.mode_order('f'), ['model', 'space'])
        self.assertEqual(queue.mode_order('b'), ['model', 'space'])
        self.assertEqual(queue.mode_order('e'), ['space', 'model'])
        self.assertEqual(queue.RECORDS, 6291456)
        self.assertEqual(queue.OPERATIONS, 1200000)

    def test_requires_complete_archive_and_data(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/queue.TRIGGER
            p.mkdir()
            self.assertFalse(queue.ready(folder))
            (p/'独立核验.json').write_text(json.dumps(dict(验收通过=True, 标签=queue.TRIGGER)))
            self.assertFalse(queue.ready(folder))
            (p/'阶段归档.json').write_text(json.dumps(dict(完整=True, 客机仍存活=True)))
            with self.assertRaises(ValueError):
                queue.ready(folder)
            (p/'阶段归档.json').write_text(json.dumps(dict(完整=True, 客机仍存活=False)))
            self.assertTrue(queue.ready(folder))

    def test_supervisor_rejects_other_users_or_commands(self):
        good = dict(PID=123, UID=os.getuid(), 启动时钟='1',
                    参数=['python3', 'research/rrzc/tests/run_ycsb_af_host.py'])
        self.assertEqual(queue.check_supervisor(good), good)
        for bad in (None, dict(good, UID=os.getuid()+1), dict(good, 参数=['qemu-system-x86_64'])):
            with self.assertRaises(ValueError):
                queue.check_supervisor(bad)

    def test_actual_process_identity_has_starttime(self):
        current = queue.process_identity(os.getpid())
        self.assertEqual(current['UID'], os.getuid())
        self.assertTrue(current['启动时钟'].isdigit())
        self.assertIsNone(queue.process_identity(2147483647))

    def test_wait_does_not_start_or_kill_guest(self):
        old = dict(PID=123, UID=os.getuid(), 启动时钟='1', 参数=['python3', 'run_ycsb_af_host.py'])
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p/'状态.json').write_text(json.dumps(dict(阶段='装载中', 最近观测={})))
            run = SimpleNamespace(record=lambda **_: None)
            with patch.object(queue, 'SOURCE', p), patch.object(queue, 'ready', return_value=False), \
                 patch.object(queue, 'process_identity', return_value=None), patch.object(queue.os, 'kill') as kill:
                with self.assertRaisesRegex(ValueError, '旧监督已退出'):
                    queue.handoff(run, old)
                kill.assert_not_called()

    def test_remote_target_uses_original_verified_program(self):
        with tempfile.TemporaryDirectory() as folder:
            run = queue.PriorityRun(SimpleNamespace(label='joint-priority-unit'), None, Path(folder), {})
            self.assertTrue(run.target.endswith('joint-ycsb32-af-v3-20261023'))
            self.assertEqual(run.state['模式顺序'][0], 'full-f-model')
            self.assertIsNone(run.state['当前客机PID'])

    def test_handoff_signals_only_supervisor_after_current_is_archived(self):
        old = dict(PID=123, UID=os.getuid(), 启动时钟='1', 参数=['python3', 'run_ycsb_af_host.py'])
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            state = dict(阶段='下一组启动', 当前阶段='full-a-model', 当前客机PID=456)
            (p/'状态.json').write_text(json.dumps(state))
            run = SimpleNamespace(record=lambda **_: None, profile={})
            def graceful_stop(pid, sig):
                self.assertEqual((pid, sig), (old['PID'], queue.signal.SIGTERM))
                # 合成finally完成事件，不关闭任何真实进程。
                (p/'状态.json').write_text(json.dumps(dict(state, 当前客机PID=None)))
            with patch.object(queue, 'SOURCE', p), patch.object(queue, 'ready', return_value=True), \
                 patch.object(queue, 'process_identity', side_effect=[old, old, None]), \
                 patch.object(queue.os, 'kill', side_effect=graceful_stop) as kill, \
                 patch.object(queue, 'host_resources', return_value={}):
                queue.handoff(run, old)
                kill.assert_called_once()


if __name__ == '__main__':
    unittest.main()
