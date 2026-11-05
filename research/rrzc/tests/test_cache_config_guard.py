"""合成日志只验证缓存准入，不代表实验结果。"""
import unittest
from cache_config_guard import check
from run_calibration_host import business_modes


LOG='''2026/10/28-00:00:00.000001 1 Options.row_cache: None
2026/10/28-00:00:00.000002 1 Options.use_direct_reads: 1
2026/10/28-00:00:00.000003 1 Options.use_direct_io_for_flush_and_compaction: 1
  no_block_cache: 1
  block_cache: (nil)
  persistent_cache: (nil)
'''


class CacheConfigGuardTest(unittest.TestCase):
    def test_actual_open_settings_are_required(self):
        self.assertTrue(check(LOG)['BlockCache关闭'])
        with self.assertRaises(AssertionError):check('只有命令行cache_size=0不能代替实际打开日志')

    def test_any_later_open_with_cache_or_buffered_io_is_rejected(self):
        for before,after in (('no_block_cache: 1','no_block_cache: 0'),
            ('block_cache: (nil)','block_cache: 0x1234'),
            ('persistent_cache: (nil)','persistent_cache: 0x1234'),
            ('Options.row_cache: None','Options.row_cache: 0x1234'),
            ('Options.use_direct_reads: 1','Options.use_direct_reads: 0'),
            ('Options.use_direct_io_for_flush_and_compaction: 1','Options.use_direct_io_for_flush_and_compaction: 0')):
            with self.subTest(field=before),self.assertRaises(AssertionError):check(LOG+LOG.replace(before,after))

    def test_default_keeps_two_greedy_arms(self):
        self.assertEqual(business_modes(False),(('g1','greedy'),('g2','greedy')))
        self.assertEqual(business_modes(True),(('g1','greedy'),('g2','blocks')))

    def test_opening_threads_are_verified_from_actual_options(self):
        text=LOG+'Options.max_file_opening_threads: 1\n'
        self.assertEqual(check(text,1)['打开文件线程数已核验'],1)
        with self.assertRaises(AssertionError):check(text,16)


if __name__=='__main__':unittest.main()
