"""分段诊断的拒绝边界；合成时点不作为实验结果。"""
from pathlib import Path
import unittest
from flush_wait_source import patched,native_command,old_flush_extracted
from analyze_flush_wait import parse

class FlushWaitTest(unittest.TestCase):
    def test_archive_member_is_not_reference_filename(self):
        self.assertFalse(old_flush_extracted('librocksdb_debug.a(format.o) /tmp/诊断-flush_job.o (symbol)\n'))
        self.assertTrue(old_flush_extracted('librocksdb_debug.a(db/flush_job.o) /tmp/a.o (symbol)\n'))

    def test_native_flags_preserved_and_ambiguous_plan_rejected(self):
        line='g++ -DNDEBUG -O2 -I. -c db/flush_job.cc -o db/flush_job.o'
        self.assertIn('-DNDEBUG',native_command(line))
        with self.assertRaises(ValueError):native_command(line+'\n'+line)
        with self.assertRaises(ValueError):native_command('echo '+line)

    def test_intervals_and_missing_boundary(self):
        phases=['构建前','构建后','日志刷新前','日志刷新后','目录同步前','目录同步后','数据库锁前','数据库锁后']
        text='\n'.join(f'Flush分段：任务=9，文件=564，阶段={p}，纳秒={i*1000000000}' for i,p in enumerate(phases))
        r=parse(text)[0];self.assertEqual(r['分段秒'],dict(构建=1,日志刷新=1,目录同步=1,数据库锁=1))
        with self.assertRaisesRegex(ValueError,'缺失'):parse(text.rsplit('\n',1)[0])
        with self.assertRaisesRegex(ValueError,'重复'):parse(text+'\n'+text.splitlines()[0])

    def test_actual_source_patch_keeps_sync_and_lock_calls(self):
        p=Path('/home/data/xwz/rrzc-baselines/joint-flush-source-v1-20261027-results/joint-flush-source-v1-20261027/部署源码/db__flush_job.cc')
        if not p.exists():self.skipTest('缺少实际部署源码')
        source=p.read_text();result=patched(source)
        for call in ('s = BuildTable(', 'LogFlush(db_options_.info_log);',
                     's = output_file_directory_->FsyncWithDirOptions(', 'db_mutex_->Lock();'):
            self.assertEqual(result.count(call),source.count(call))
        self.assertEqual(result.count('auto trace_phase='),1)
        with self.assertRaisesRegex(ValueError,'锚点不唯一'):patched(result)

if __name__=='__main__':unittest.main()
