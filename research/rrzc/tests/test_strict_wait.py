"""严格RR开放SST等待的可逆补丁和真实并发单元回归。"""
from pathlib import Path
import subprocess
import tempfile
import unittest
from strict_wait_patch import make_patch,patched
ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('/home/data/xwz/rrzc-baselines/joint-rr-coverage-inspect-v1-20261024-results/源码')


class StrictWaitTest(unittest.TestCase):
    def test_real_guest_uses_required_scheduler_and_cxx_standard(self):
        text=(ROOT/'tests/run_strict_wait_host.py').read_text()
        self.assertIn('g++ -std=c++20',text)
        self.assertLess(text.index("printf 'mq-deadline"),text.index('plugin/zenfs/util/zenfs mkfs'))

    def test_concurrent_wait_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            binary=Path(folder)/'test'
            subprocess.run(['g++','-std=c++17','-pthread','-Wall','-Wextra','-Werror',
                '-fsanitize=undefined','-I'+str(ROOT/'model'),str(ROOT/'tests/test_strict_wait.cc'),'-o',str(binary)],check=True)
            output=subprocess.check_output([str(binary)],text=True,timeout=5)
            self.assertIn('超时及真实错误拒绝继续',output)

    def test_patch_reversible_and_keeps_threshold_and_coverage(self):
        if not SOURCE.is_dir():self.skipTest('没有实测父源归档')
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder);(target/'fs').mkdir()
            for name in ('io_zenfs.cc','fs_zenfs.cc','zbd_zenfs.h'):(target/'fs'/name).write_bytes((SOURCE/name).read_bytes())
            patch=make_patch(SOURCE)
            subprocess.run(['git','apply','-'],cwd=target,input=patch,text=True,check=True)
            for name in ('io_zenfs.cc','fs_zenfs.cc','zbd_zenfs.h'):
                actual=(target/'fs'/name).read_text();self.assertEqual(actual,patched(name,(SOURCE/name).read_text()))
            text=(target/'fs/fs_zenfs.cc').read_text()
            self.assertIn('covered_bytes[zone_it->start] != zone_it->used_capacity',text)
            self.assertIn('p.blocks_at_threshold',text)
            self.assertIn('*complete=!found',text)
            subprocess.run(['git','apply','--reverse','-'],cwd=target,input=patch,text=True,check=True)
            for name in ('io_zenfs.cc','fs_zenfs.cc','zbd_zenfs.h'):self.assertEqual((target/'fs'/name).read_bytes(),(SOURCE/name).read_bytes())


if __name__=='__main__':unittest.main()
