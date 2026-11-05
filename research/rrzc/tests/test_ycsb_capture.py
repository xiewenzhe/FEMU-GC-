"""异常路径日志保护、有限正常关机及停顿关联；合成日志仅用于单元测试。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import capture_ycsb_guest as guest
import run_rr_fair_long_v2_host as host
from analyze_ycsb_stalls import analyze_stalls


class CaptureTest(unittest.TestCase):
    def prepare(self, tmp):
        root = Path(tmp)
        result, temporary, target = [root/n for n in ('result', 'tmp', 'target')]
        for p in (result/'aux/rrzc-ycsb', temporary, target):
            p.mkdir(parents=True)
        old, active = [temporary/('zenfs_nvme0n1_'+n+'.log') for n in ('old', 'active')]
        old.write_text('旧会话，不得误归档')
        active.write_text('RR 开始：\nRR 完成：\nZC 统计：\n')
        (result/'logs.before.txt').write_text(str(old)+'\n')
        (result/'ycsb.log').write_text('Load runtime(sec): 3\n2026-10-21 00:00:10 10 sec: 100 operations;\n')
        (result/'aux/rrzc-ycsb/LOG').write_text('真实业务尚未返回的测试占位日志')
        return root, result, temporary, target, active

    def test_active_log_does_not_wait_for_wrapper_or_pick_old_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, result, temporary, target, active = self.prepare(tmp)
            self.assertEqual(guest.active_log(result, temporary), active)
            self.assertFalse((result/'finished').exists())
            with patch.object(guest, 'thread_state', return_value=[]), patch.object(guest.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], 0, '{}', '')):
                report = guest.capture(result, root/'capture', target, temporary=temporary)
            self.assertTrue(report['完整'])
            self.assertFalse(report['业务包装已结束'])
            self.assertEqual((root/'capture/zenfs.partial.log').read_bytes(), active.read_bytes())
            self.assertEqual(report['文件']['zenfs.partial.log']['SHA256'], hashlib.sha256(active.read_bytes()).hexdigest())
            self.assertFalse((result/'finished').exists())
            self.assertFalse((result/'zenfs.run.log').exists())

    def test_ambiguous_logs_are_unknown_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, result, temporary, _, _ = self.prepare(tmp)
            (temporary/'zenfs_nvme0n1_second.log').write_text('另一份未知日志')
            with self.assertRaisesRegex(RuntimeError, '唯一定位'):
                guest.active_log(result, temporary)

    def test_device_timeout_preserves_logs_but_rejects_complete_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, result, temporary, target, _ = self.prepare(tmp)
            with patch.object(guest, 'thread_state', return_value=[]), patch.object(guest.subprocess, 'run',
                    side_effect=subprocess.TimeoutExpired('只读探测', 8)):
                report = guest.capture(result, root/'capture', target, temporary=temporary)
            self.assertFalse(report['完整'])
            self.assertEqual(len(report['错误']), 2)
            self.assertTrue((root/'capture/zenfs.partial.log').is_file())
            with self.assertRaises(FileExistsError):
                guest.capture(result, root/'capture', target, temporary=temporary)

    def test_maintenance_counts_do_not_invent_business_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, result, temporary, _, active = self.prepare(tmp)
            with patch.object(guest, 'active_log', return_value=active):
                before = guest.progress(result)
                active.write_text(active.read_text()+'RR 完成：\n')
                after = guest.progress(result)
            self.assertEqual(before['最近状态'], after['最近状态'])
            self.assertEqual(after['RR完成'], before['RR完成']+1)


class SupervisorTest(unittest.TestCase):
    def make_run(self, output):
        return host.CollectedRun(SimpleNamespace(label='joint-unit-collected'), None, output, {})

    def test_new_time_budget_does_not_change_parent_or_binaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            self.assertEqual(run.state['长测上限秒'], 3600)
            self.assertEqual(run.state['无进展上限秒'], 180)
            self.assertEqual(run.target, host.old.TARGET)
            self.assertEqual(run.parent_image, host.old.PARENT)
            self.assertNotEqual(run.control, run.target)

    def test_clock_tick_and_background_work_are_not_business_progress(self):
        point = dict(装载完成=True, 最近状态=['10', '1234'], RR完成=1)
        later = dict(装载完成=True, 最近状态=['190', '1234'], RR完成=20)
        self.assertEqual(host.business_key(point), host.business_key(later))
        later['最近状态'][1] = '1235'
        self.assertNotEqual(host.business_key(point), host.business_key(later))

    def test_pid_or_image_mismatch_does_not_send_poweroff(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            run.state.update(当前客机PID=os.getpid(), 当前镜像='/不匹配的镜像')
            with patch.object(run, 'shutdown') as shutdown, patch.object(run, 'root') as root:
                with self.assertRaisesRegex(RuntimeError, 'PID、账号或镜像不符'):
                    run.close_guest()
                shutdown.assert_not_called()
                root.assert_not_called()

    def test_delayed_qmp_exit_is_not_misreported_as_sudo_auth_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            run.state.update(当前客机PID=os.getpid(), 当前镜像='/单测镜像')
            identity = b'file=/\xe5\x8d\x95\xe6\xb5\x8b\xe9\x95\x9c\xe5\x83\x8f,if=none,aio=native,cache=none,format=qcow2,id=hd0\0'
            with patch.object(Path, 'read_bytes', return_value=identity), \
                 patch.object(run, 'shutdown', side_effect=RuntimeError('正常退出仍在等待')), \
                 patch.object(host, 'wait_gone', return_value=True) as waited, patch.object(run, 'root') as root:
                run.close_guest()
            waited.assert_called_once_with(os.getpid(), 150)
            root.assert_not_called()

    def test_archive_error_still_closes_vm_and_remains_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            run = self.make_run(output)
            with patch.object(run, 'collect', side_effect=RuntimeError('归档传输失败')), patch.object(run, 'close_guest') as close:
                with self.assertRaisesRegex(RuntimeError, '停止后续阶段'):
                    run.finish(output)
            close.assert_called_once()
            self.assertFalse(json.loads((output/'阶段归档.json').read_text())['完整'])

    def test_original_error_survives_cleanup_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output/'同库身份.json').write_text('{}')
            run = self.make_run(output)
            with patch.object(run, 'start', side_effect=ValueError('原始监督错误')), \
                 patch.object(run, 'finish', side_effect=RuntimeError('附加收尾错误')) as finish:
                with self.assertRaisesRegex(ValueError, '原始监督错误'):
                    run.arm('short', 'greedy')
            finish.assert_called_once()
            self.assertIn('附加收尾错误', run.state['收尾另有错误'])
            self.assertEqual(run.state['已完成'], [])


class StallAnalysisTest(unittest.TestCase):
    def test_overlap_is_reported_as_association_not_causality(self):
        zen = '\n'.join([
            '2026/10/21-00:00:01.000000 1 RR 开始：Zone 123，',
            '2026/10/21-00:00:01.500000 2 空间紧急分配等待：等待数=1',
            '2026/10/21-00:00:02.000000 1 RR 完成：Zone 123 已重置',
            '2026/10/21-00:00:02.100000 1 ZC 统计：'])
        rock = '\n'.join([
            '2026/10/21-00:00:01.400000 2 Stopping writes because we have 3 immutable memtables',
            '2026/10/21-00:00:02.500000 2 Flush lasted 1800000 microseconds'])
        result = analyze_stalls(rock, zen)
        self.assertEqual(result['已配对RR区间数'], 1)
        self.assertEqual(result['最长十次Flush'][0]['区间内分配等待登记数'], 1)
        self.assertEqual(result['最长十次Flush'][0]['与已完成RR区间重合数'], 1)
        self.assertIn('不证明', result['边界'])


if __name__ == '__main__':
    unittest.main()
