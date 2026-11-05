"""目标扫描采证不得新增取锁或修改筛选、同分和返回逻辑。"""
from pathlib import Path
import unittest
from target_alloc_source import patched,controlled_retry


class TargetSourceTest(unittest.TestCase):
    def test_real_source_acquires_and_ranking_are_unchanged(self):
        path=Path('/home/data/xwz/rrzc-baselines/joint-controlled-aux-tmpfs-v1-20261027-results/joint-controlled-aux-tmpfs-v1-20261027/修复后-zbd_zenfs.cc')
        if not path.exists():self.skipTest('部署源码尚未归档')
        before=path.read_text();after=patched(before)
        for marker in ('z->Acquire()', 'z->CheckRelease()', 'if (diff <= best_diff)',
            'z->capacity_ >= min_capacity','migration_reserve_zone_ = replacement;',
            'migrate_resource_.wait(lock, [this] { return !migrating_; });'):
            self.assertEqual(before.count(marker),after.count(marker))
        self.assertIn('trace_target?&target_observation:nullptr',after)
        self.assertIn('RRZC_CAL_TARGET_TRACE',after)
        self.assertNotIn('StrictLock',after.split('迁移目标扫描：',1)[0].split('GetBestOpenZoneMatch',1)[1])
        # 普通分配和预留替换整个后续实现逐字保持；只增加匹配函数及迁移入口诊断。
        marker='  if (migration_reserve_enabled_ && s.ok() && *out_zone == nullptr) {'
        self.assertEqual(before.split(marker,1)[1],after.split(marker,1)[1])
        with self.assertRaises(ValueError):patched(after)

    def test_controlled_retry_releases_before_sleep(self):
        native='''          for (unsigned int attempt = 0; attempt < attempts; ++attempt) {
            if (attempt + 1 < attempts) usleep(50 * 1000);
          }'''
        result=controlled_retry(native)
        self.assertIn('if (boundary_path && boundary_path[0])',result)
        self.assertLess(result.index('boundary_retry.unlock()'),result.index('usleep(50 * 1000)'))
        self.assertEqual(result.count('usleep(50 * 1000)'),1)


if __name__=='__main__':unittest.main()
