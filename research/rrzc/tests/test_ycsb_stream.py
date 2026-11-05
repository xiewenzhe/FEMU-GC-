"""增量采样和只读重试的合成回归；不伪造性能结果或修改旧证据。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tests'))
import capture_ycsb_stream_guest as guest
from run_ycsb_stream_host import bounded_query, ReadOnlyFailure, StreamRun


class StreamTest(unittest.TestCase):
    def prepare(self, folder):
        p = Path(folder)
        result, temporary, target = [p/n for n in ('result', 'tmp', 'target')]
        for d in (result/'aux/rrzc-ycsb', temporary, target):
            d.mkdir(parents=True)
        active = temporary/'zenfs_nvme0n1_active.log'
        active.write_text('RR 开始：一次\nRR 完成：一次\nZC 统计：一次\n')
        (result/'logs.before.txt').write_text('')
        (result/'ycsb.log').write_text('Load runtime(sec): 1\n1 sec: 10 operations;\n')
        (result/'aux/rrzc-ycsb/LOG').write_text('无错误\n')
        return p, result, temporary, target, active

    def test_incremental_counts_and_no_new_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            p, result, tmp, _, active = self.prepare(folder)
            cache = p/'cache.json'
            a = guest.progress(result, cache=cache, temporary=tmp)
            b = guest.progress(result, cache=cache, temporary=tmp)
            self.assertEqual(b['采样本次读取字节'], 0)
            self.assertEqual((a['RR完成'], b['RR完成'], b['ZC完成']), (1, 1, 1))
            with active.open('a') as stream:
                stream.write('RR 完成：第二次\n')
            c = guest.progress(result, cache=cache, temporary=tmp)
            self.assertEqual(c['RR完成'], 2)
            self.assertEqual(c['采样本次读取字节'], len('RR 完成：第二次\n'.encode()))
            self.assertEqual(c['最近状态'], a['最近状态'])

    def test_split_utf8_and_incomplete_line(self):
        with tempfile.TemporaryDirectory() as folder:
            p, result, tmp, _, active = self.prepare(folder)
            cache = p/'cache.json'
            guest.progress(result, cache=cache, temporary=tmp)
            raw = 'RR 开始：跨边界\n'.encode()
            for block in (raw[:4], raw[4:-1], raw[-1:]):
                with active.open('ab') as stream:
                    stream.write(block)
                final = guest.progress(result, cache=cache, temporary=tmp)
            self.assertEqual(final['RR开始'], 2)

    def test_startup_without_result_or_log_waits_then_counts_real_business(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            result, tmp, cache = p/'result', p/'tmp', p/'cache.json'
            tmp.mkdir()
            old = tmp/'zenfs_nvme0n1_mkfs.log'
            old.write_text('RR 完成：旧mkfs不计入业务\n')
            a = guest.progress(result, cache=cache, temporary=tmp)
            self.assertIsNone(a['维护日志'])
            result.mkdir()
            (result/'logs.before.txt').write_text(str(old)+'\n')
            b = guest.progress(result, cache=cache, temporary=tmp)
            self.assertIsNone(b['维护日志'])
            active = tmp/'zenfs_nvme0n1_business.log'
            active.write_text('RR 完成：实际业务\n')
            c = guest.progress(result, cache=cache, temporary=tmp)
            self.assertEqual(c['RR完成'], 1)
            self.assertEqual(c['维护日志状态'], '已建立')

    def test_disappeared_business_log_is_not_startup_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            p, result, tmp, _, active = self.prepare(folder)
            cache = p/'cache.json'
            guest.progress(result, cache=cache, temporary=tmp)
            active.unlink()
            with self.assertRaisesRegex(RuntimeError, '候选数=0'):
                guest.progress(result, cache=cache, temporary=tmp)

    def test_multiple_business_logs_remain_fatal(self):
        with tempfile.TemporaryDirectory() as folder:
            p, result, tmp, _, _ = self.prepare(folder)
            (tmp/'zenfs_nvme0n1_ambiguous.log').write_text('')
            with self.assertRaisesRegex(RuntimeError, '候选数=2'):
                guest.progress(result, cache=p/'cache.json', temporary=tmp)

    def test_finished_without_business_log_remains_fatal(self):
        with tempfile.TemporaryDirectory() as folder:
            p, result, tmp, _, active = self.prepare(folder)
            active.unlink()
            (result/'finished').touch()
            with self.assertRaisesRegex(RuntimeError, '候选数=0'):
                guest.progress(result, cache=p/'cache.json', temporary=tmp)

    def test_finished_log_hardlink_not_double_counted(self):
        with tempfile.TemporaryDirectory() as folder:
            p, result, tmp, _, active = self.prepare(folder)
            cache = p/'cache.json'
            guest.progress(result, cache=cache, temporary=tmp)
            os.link(active, result/'zenfs.run.log')
            (result/'finished').touch()
            point = guest.progress(result, cache=cache, temporary=tmp)
            self.assertEqual(point['RR完成'], 1)
            self.assertEqual(point['采样本次读取字节'], 0)

    def test_truncation_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            p, result, tmp, _, active = self.prepare(folder)
            cache = p/'cache.json'
            guest.progress(result, cache=cache, temporary=tmp)
            active.write_text('')
            with self.assertRaisesRegex(RuntimeError, '替换或截断'):
                guest.progress(result, cache=cache, temporary=tmp)

    def test_errors_are_not_lost_on_later_sampling(self):
        with tempfile.TemporaryDirectory() as folder:
            p, result, tmp, _, _ = self.prepare(folder)
            cache = p/'cache.json'
            guest.progress(result, cache=cache, temporary=tmp)
            with (result/'aux/rrzc-ycsb/LOG').open('a') as stream:
                stream.write('Background compaction error 合成错误\n')
            a = guest.progress(result, cache=cache, temporary=tmp)
            b = guest.progress(result, cache=cache, temporary=tmp)
            self.assertTrue(a['错误'])
            self.assertEqual(a['错误'], b['错误'])

    def test_capture_hashes_without_full_file_read(self):
        with tempfile.TemporaryDirectory() as folder:
            p, result, tmp, target, active = self.prepare(folder)
            (result/'finished').touch()
            with patch.object(guest, 'thread_state', return_value=[]), \
                 patch.object(guest.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{}', '')), \
                 patch.object(Path, 'read_bytes', side_effect=AssertionError('禁止整文件读')):
                report = guest.capture(result, p/'capture', target, temporary=tmp)
            self.assertTrue(report['完整'])
            self.assertEqual((p/'capture/zenfs.partial.log').stat().st_ino, active.stat().st_ino)
            self.assertEqual(report['文件']['zenfs.partial.log']['SHA256'],
                             hashlib.sha256(active.read_bytes()).hexdigest())

    def test_transport_recovers_only_255_and_preserves_feedback(self):
        calls, events = [], []
        results = [subprocess.CompletedProcess([], 255, ''), subprocess.CompletedProcess([], 0, '真实只读结果')]
        def once(cmd):
            calls.append(cmd)
            return results.pop(0)
        out = bounded_query('只读进度', once, lambda **e: events.append(e), wait=lambda _: None)
        self.assertEqual(out, '真实只读结果')
        self.assertEqual(len(calls), 2)
        self.assertEqual(events[0]['退出码'], 255)

    def test_transport_business_error_is_not_retried(self):
        events = []
        with self.assertRaises(ReadOnlyFailure):
            bounded_query('只读进度', lambda _: subprocess.CompletedProcess([], 1, '真实诊断失败'),
                          lambda **e: events.append(e), wait=lambda _: None)
        self.assertEqual(len(events), 1)

    def test_transport_retry_is_bounded(self):
        events = []
        with self.assertRaises(ReadOnlyFailure):
            bounded_query('只读进度', lambda _: subprocess.CompletedProcess([], 255, '断开'),
                          lambda **e: events.append(e), wait=lambda _: None)
        self.assertEqual(len(events), 3)

    def test_stream_run_keeps_original_binary_and_changes_only_wrapper(self):
        with tempfile.TemporaryDirectory() as folder:
            run = StreamRun(SimpleNamespace(label='joint-stream-unit'), None, Path(folder), {})
            self.assertTrue(run.target.endswith('joint-ycsb32-af-v3-20261023'))
            command = f'nohup setsid bash {run.target}/run_ycsb_af_guest.sh smoke-f-model model f 8192 8192 20261023'
            with patch('run_ycsb_af_host.SuiteRun.root', return_value='已启动') as root:
                run.root(command)
            self.assertIn('run_ycsb_af_stream_guest.sh', root.call_args.args[0])
            self.assertIn('8192 8192 20261023', root.call_args.args[0])

    def test_wrapper_unchanged_mechanism_environment(self):
        import re
        old = (ROOT/'tests/run_ycsb_af_guest.sh').read_text()
        new = (ROOT/'tests/run_ycsb_af_stream_guest.sh').read_text()
        pattern = r'(ZENFS_[A-Z0-9_]+)=([^\s\\]+)'
        self.assertEqual(re.findall(pattern, old), re.findall(pattern, new))
        subprocess.run(['bash', '-n', str(ROOT/'tests/run_ycsb_af_stream_guest.sh')], check=True)


if __name__ == '__main__':
    unittest.main()
