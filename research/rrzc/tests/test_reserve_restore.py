"""真实恢复源码补丁往返及同一修复函数编译，不能替代实际设备验证。"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from reserve_restore_patch import make_patch

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('/home/data/xwz/rrzc-baselines/joint-repeat-order-v1-20261026-results/源码恢复-a2/joint-repeat-evidence-v2-20261026-a2/fs')


class RestoreTest(unittest.TestCase):
    def test_compiled_same_restore_function(self):
        with tempfile.TemporaryDirectory(prefix='rrzc-reserve-restore-') as folder:
            exe=Path(folder)/'check'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_reserve_restore.cc'),'-o',str(exe)],check=True)
            self.assertIn('边界通过',subprocess.check_output([str(exe)],text=True))

    def test_actual_source_patch_roundtrip(self):
        if not SOURCE.is_dir():self.skipTest('未取回实际部署源')
        names=('fs_zenfs.cc','zbd_zenfs.cc','zbd_zenfs.h')
        original=[(SOURCE/n).read_text() for n in names]
        patch=make_patch(*original)
        with tempfile.TemporaryDirectory(prefix='rrzc-restore-patch-') as folder:
            target=Path(folder);(target/'fs').mkdir()
            for n in names:shutil.copyfile(SOURCE/n,target/'fs'/n)
            p=target/'change.patch';p.write_text(patch)
            for args in (['--check'],[],['--reverse','--check'],['--reverse']):
                subprocess.run(['git','apply',*args,str(p)],cwd=target,check=True)
            for n,raw in zip(names,original):self.assertEqual((target/'fs'/n).read_text(),raw)


if __name__=='__main__':unittest.main()
