"""合成边界测试，只验证分析逻辑，不作为设备实验数据。"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_sst_survival import SECOND, WINDOW, evaluate, features, label, parse_rocks, parse_zenfs, stamp


class SstSurvivalTest(unittest.TestCase):
    def test_stamp_preserves_microseconds(self):
        self.assertEqual(stamp('1970/01/01-00:00:01.123456 日志'), 1_123_456)

    def test_future_retirement_is_label_not_feature(self):
        file = dict(创建可见微秒=SECOND, Compaction=[])
        before = features(file, 5*SECOND)
        file['退役微秒'] = 6*SECOND
        self.assertEqual(features(file, 5*SECOND), before)
        self.assertEqual(label(file, 5*SECOND, WINDOW, 20*SECOND), 1)

    def test_window_not_fully_observed_is_not_survival(self):
        file = {}
        self.assertIsNone(label(file, 15*SECOND, WINDOW, 20*SECOND))
        self.assertEqual(label(file, 10*SECOND, WINDOW, 20*SECOND), 0)
        file['退役微秒'] = 18*SECOND
        self.assertEqual(label(file, 15*SECOND, WINDOW, 20*SECOND), 1)
        self.assertIsNone(label(file, 19*SECOND, WINDOW, 20*SECOND))
        self.assertIsNone(label(file, 15*SECOND, WINDOW, 17*SECOND))

    def test_active_state_uses_only_visible_start_and_finish(self):
        file = dict(创建可见微秒=SECOND, Compaction=[
            dict(任务=7, 开始微秒=10*SECOND, 结束微秒=20*SECOND)])
        self.assertFalse(features(file, 10*SECOND)['已进入进行中Compaction'])
        self.assertTrue(features(file, 15*SECOND)['已进入进行中Compaction'])
        # 将未来结束延长不能影响现在的状态。
        file['Compaction'][0]['结束微秒'] = 30*SECOND
        self.assertTrue(features(file, 15*SECOND)['已进入进行中Compaction'])
        self.assertFalse(features(file, 31*SECOND)['已进入进行中Compaction'])
        with self.assertRaises(ValueError):
            features(file, SECOND)

    def test_temporal_split_no_same_file_or_future_label_leakage(self):
        def sample(fid, when, death=None):
            r = dict(SST编号=fid, 观测微秒=when*SECOND, 区内有效字节='10',
                     年龄分组='小于10秒', 已进入进行中Compaction=False)
            if death is not None:
                r['退役微秒'] = death*SECOND
            return r
        data = [sample(1, 1, 5), sample(2, 15, 22), sample(1, 25, 50),
                sample(3, 25), sample(4, 45)]
        report = evaluate(data, 20*SECOND, 50*SECOND)
        self.assertEqual(report['训练首次文件数'], 2)
        self.assertEqual(report['训练完整窗口数'], 1)
        self.assertEqual(report['验证首次新文件数'], 2)
        self.assertEqual(report['验证右删失数'], 1)
        self.assertEqual({r['SST编号'] for r in report['验证明细']}, {3, 4})
        self.assertEqual(report['验证明细'][0]['预测十秒退役概率']['训练期总体'], 1)
        data[1]['退役微秒'] = 49*SECOND
        again = evaluate(data, 20*SECOND, 50*SECOND)
        self.assertEqual(report['误差'], again['误差'])

    def test_near_boundary_failures_also_excluded_from_training_and_scoring(self):
        def row(fid, when, death=None):
            r = dict(SST编号=fid, 观测微秒=when*SECOND, 区内有效字节='10',
                     年龄分组='小于10秒', 已进入进行中Compaction=False)
            if death is not None:
                r['退役微秒'] = death*SECOND
            return r
        r = evaluate([row(1, 1), row(2, 15, 19), row(3, 25), row(4, 45, 49)],
                     20*SECOND, 50*SECOND)
        self.assertEqual(r['训练完整窗口数'], 1)
        self.assertEqual(r['验证完整窗口数'], 1)
        self.assertEqual(r['验证不足完整窗口数'], 1)
        self.assertEqual(r['验证右删失数'], 0)
        self.assertEqual(r['误差']['训练期总体']['Brier'], 0)

    def test_shutdown_cancelled_empty_file_not_successful_sst(self):
        text = ('RocksDB version: 10\n'
                'SST files in /rrzc-ycsb dir, Total Num: 0, files:\n'
                '2026/10/20-01:00:00.000000 1 EVENT_LOG_v1 ')
        event = dict(time_micros=stamp('2026/10/20-01:00:00.000000'),
                     event='table_file_creation', file_number=8, file_size=0,
                     status='Shutdown in progress: Database shutdown', cf_name='default')
        tail = '\n2026/10/20-01:00:09.000000 1 Shutdown complete\n'
        files, _, _ = parse_rocks(text+json.dumps(event)+tail)
        self.assertFalse(files[8]['创建成功'])
        event['status'] = 'IO error: write failed'
        with self.assertRaises(ValueError):
            parse_rocks(text+json.dumps(event)+tail)

    def test_delayed_original_event_not_visible_early(self):
        prefix = '2026/10/20-01:00:05.000000 1 '
        original = stamp('2026/10/20-01:00:00.000000')
        text = 'RocksDB version: 10\nSST files in /rrzc-ycsb dir, Total Num: 0, files:\n'
        text += prefix + 'EVENT_LOG_v1 ' + json.dumps(dict(time_micros=original,
            event='table_file_creation', file_number=8, file_size=1024, status='OK', cf_name='default'))
        text += '\n2026/10/20-01:00:09.000000 1 Shutdown complete\n'
        files, _, _ = parse_rocks(text)
        self.assertEqual(files[8]['创建可见微秒'], original+5*SECOND)
        with self.assertRaises(ValueError):
            features(files[8], original+SECOND)
        with self.assertRaises(ValueError):
            parse_rocks(text.replace('Shutdown complete', '非正常停止'))
        with self.assertRaises(ValueError):
            parse_rocks(text+'RocksDB version: 10\n')

    def test_filename_not_zenfs_number_is_database_identity(self):
        text = ('2026/10/20-01:00:00.000000 1 V1后续文件退役：文件编号=900，'
                '文件名=/rrzc-ycsb/000008.sst，最后链接已删除，元数据持久化成功\n')
        retired, _, _ = parse_zenfs(text)
        self.assertEqual(set(retired), {8})
        self.assertEqual(retired[8]['ZenFS编号'], '900')


if __name__ == '__main__':
    unittest.main()
