"""独立副本接线锚点检查；不冒充真实设备功能或布局收益。"""
from pathlib import Path
import subprocess
import tempfile
import unittest
from colocation_source import zbd_header,zbd_source,fs_source

BASE=Path('/home/data/xwz/rrzc-baselines/joint-repeat-order-v6-20261026-results/源码')


class ColocationSourceTest(unittest.TestCase):
    def test_frozen_native_allocation_and_gc_selection_are_preserved(self):
        if not BASE.exists():self.skipTest('原始部署源缺失')
        h=(BASE/'修复后-zbd_zenfs.h').read_text()
        cc=(BASE/'修复后-zbd_zenfs.cc').read_text()
        out=zbd_source(cc)
        self.assertEqual(out.count('FinishCheapestIOZone();'),cc.count('FinishCheapestIOZone();'))
        self.assertIn('if(!status.ok()){PutOpenIOZoneToken();return status;}',out)
        self.assertIn('if(zone && coloc_migration_targets_.erase(zone))',out)
        self.assertIn('ColocationPool()',zbd_header(h))
        with self.assertRaises(ValueError):zbd_source(out)
        fs=(BASE/'修复后-fs_zenfs.cc').read_text();changed=fs_source(fs)
        before=fs.split('void ZenFS::GCWorker()',1)[1].split('Status ZenFS::',1)[0]
        after=changed.split('void ZenFS::GCWorker()',1)[1].split('Status ZenFS::',1)[0]
        self.assertEqual(before,after)

    def test_pool_resource_and_generation_boundaries(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            binary=Path(directory)/'pool'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(root/'model'),str(root/'tests/test_colocation_pool.cc'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)


if __name__=='__main__':unittest.main()
