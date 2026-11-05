"""论文参数源及容量守恒测试；不把几何计算称为真实设备验收。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finerr_profile import load, settings


class FineRRProfileTest(unittest.TestCase):
    def test_capacity_and_units(self):
        profile = load()
        self.assertEqual(profile['命名空间']['容量字节'], 32 << 30)
        self.assertEqual(profile['命名空间']['Zone字节'], 128 << 20)
        self.assertEqual(profile['命名空间']['每Zone物理块数'], 128)
        self.assertEqual(profile['维护']['模型最小回收字节'], 0)
        self.assertEqual(settings(profile)['公共ZenFS环境']['ZENFS_RRZC_RECLAIM_BYTES'], '0')

    def test_device_options_are_not_bbssd_options(self):
        opts = settings(load())['设备参数'].split(',')
        for value in ('devsz_mb=32768', 'zns_num_lun=4', 'zns_num_plane=4',
                      'zns_num_blk=256', 'zns_page_size=4096', 'zns_pgs_per_blk=256',
                      'zns_rr_threshold=1024', 'zns_pg_rd_lat=40000',
                      'zns_pg_wr_lat=200000', 'zns_blk_er_lat=2000000',
                      'zns_max_open=8', 'zns_max_active=12'):
            self.assertIn(value, opts)
        self.assertNotIn('pg_rd_lat=40000', opts)

    def test_legacy_profile_keeps_original_open_and_active_limits(self):
        profile = copy.deepcopy(load())
        profile['沿用条件'].pop('最大打开Zone数')
        profile['沿用条件']['最大活动Zone数'] = 6
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '旧配置.json'
            path.write_text(json.dumps(profile))
            opts = settings(load(path))['设备参数'].split(',')
        self.assertIn('zns_max_open=0', opts)
        self.assertIn('zns_max_active=6', opts)

    def test_reject_limits_that_break_resource_accounting(self):
        for active, opened in ((12, 13), (12, 3), (2, 0),
                               (12, -1), (257, 8), (True, 8)):
            profile = copy.deepcopy(load())
            profile['沿用条件'].update(最大活动Zone数=active, 最大打开Zone数=opened)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / '错误配置.json'
                path.write_text(json.dumps(profile))
                with self.assertRaises(ValueError):
                    load(path)

    def test_reject_legacy_values_silently_mixed_into_new_profile(self):
        original = load()
        for section, key, value in (('命名空间', 'Zone数量', 32),
                                    ('命名空间', '每Zone物理块数', 64),
                                    ('维护', '模型最小回收字节', 192 << 20),
                                    ('硬件', '每PlaneBlock数', 255),
                                    ('硬件', '物理页字节', 16384)):
            profile = copy.deepcopy(original)
            profile[section][key] = value
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / '配置.json'
                path.write_text(json.dumps(profile))
                with self.assertRaises(ValueError):
                    load(path)


if __name__ == '__main__':
    unittest.main()
