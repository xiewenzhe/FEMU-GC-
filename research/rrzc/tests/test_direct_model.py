"""真实编译统一模型并核验补丁可回退；合成候选不代表设备收益。"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]


class DirectModelTest(unittest.TestCase):
    def test_spare_test_uses_public_release(self):
        source=(ROOT/'tests/direct_spare_smoke.cc').read_text()
        self.assertNotIn('->CheckRelease()',source)
        self.assertIn('require(live->Release()',source)

    def test_polling_tolerates_snapshot_omission_during_migration(self):
        source=(ROOT/'tests/direct_spare_smoke.cc').read_text()
        for body in source.split('while(Clock::now()<limit) {')[1:]:
            body=body.split('\n  }',1)[0]
            self.assertNotIn('extent(fs,',body)
            self.assertIn('find(fs,',body)
        self.assertIn('observed && observed->length==32*MiB',source)
        self.assertIn('observed->length==62*MiB',source)

    def test_seed_preparation_keeps_space_pressure_with_real_writes(self):
        source=(ROOT/'tests/direct_spare_smoke.cc').read_text()
        body=source.split('while(Clock::now()<limit) {',1)[1].split('require(primed,',1)[0]
        self.assertIn('while(filled<completed)',body)
        self.assertIn('ZoneBytes,122+filled,Env::WLTH_MEDIUM',body)
        self.assertIn('primed=completed==4 && filled==4',body)

    def test_actual_cpp(self):
        with tempfile.TemporaryDirectory(prefix='rrzc-direct-test-') as tmp:
            binary=Path(tmp)/'unit'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_direct_selector.cc'),'-o',str(binary)],check=True,capture_output=True)
            self.assertIn('无Greedy前置',subprocess.check_output([str(binary)],text=True))

    def test_patch_on_frozen_parent_and_reverse(self):
        parent=Path('/home/data/xwz/rrzc-baselines/benefit-device-20261019-results/build/benefit-device-20261019/fs_zenfs.compiled.cc')
        if not parent.is_file():self.skipTest('当前环境没有冻结父源码，不能伪造验证')
        with tempfile.TemporaryDirectory(prefix='rrzc-direct-patch-') as tmp:
            dest=Path(tmp)/'fs';dest.mkdir();shutil.copyfile(parent,dest/'fs_zenfs.cc')
            patch=(ROOT/'zenfs-direct-selector.patch').read_text()
            subprocess.run(['git','apply','--unidiff-zero'],input=patch,
                cwd=tmp,text=True,capture_output=True,check=True)
            body=(dest/'fs_zenfs.cc').read_text().split('void ZenFS::GCWorker() {',1)[1]
            parse=body.split('strtoull(value, &end, 10);',1)[1].split('} else simple_enabled',1)[0]
            self.assertNotIn('simple_budget > 0',parse)
            self.assertIn("errno == 0 && end && *end == '\\0'",parse)
            self.assertIn('if (free_percent > GC_START_LEVEL) continue;',body)
            subprocess.run(['git','apply','--unidiff-zero','--reverse'],input=patch,
                cwd=tmp,text=True,capture_output=True,check=True)
            self.assertEqual(parent.read_bytes(),(dest/'fs_zenfs.cc').read_bytes())

    def test_spare_observation_patch_is_read_only_and_reversible(self):
        parent=ROOT.parents[1]/'build-femu/rrzc/migration-spare-work/fs'
        if not parent.is_dir():self.skipTest('当前环境没有真实公共分配器源码')
        with tempfile.TemporaryDirectory(prefix='rrzc-spare-patch-') as tmp:
            dest=Path(tmp)/'fs';dest.mkdir()
            for name in ('zbd_zenfs.cc','zbd_zenfs.h'):shutil.copyfile(parent/name,dest/name)
            patch=(ROOT/'zenfs-direct-spare-observe.patch').read_text()
            subprocess.run(['git','apply','--unidiff-zero'],input=patch,cwd=tmp,text=True,capture_output=True,check=True)
            body=(dest/'zbd_zenfs.cc').read_text().split('ZonedBlockDevice::GetMigrationSpareObservation() {')[1].split('\n}\n',1)[0]
            for forbidden in ('->Acquire(','->Release(','EnsureMigrationReserve(', '->Finish(', '->Reset(', '->Append('):
                self.assertNotIn(forbidden,body)
            subprocess.run(['git','apply','--unidiff-zero','--reverse'],input=patch,cwd=tmp,text=True,capture_output=True,check=True)
            for name in ('zbd_zenfs.cc','zbd_zenfs.h'):self.assertEqual((parent/name).read_bytes(),(dest/name).read_bytes())


if __name__=='__main__':unittest.main()
