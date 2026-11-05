"""编译真实C++近期ETA内核，并检查新旧接线可逆；不编造设备结果。"""
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class RecentEtaTest(unittest.TestCase):
    def test_actual_cpp(self):
        with tempfile.TemporaryDirectory(prefix='rrzc-recent-eta-') as tmp:
            binary=Path(tmp)/'model'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_recent_eta_selector.cc'),'-o',str(binary)],
                check=True,capture_output=True)
            self.assertIn('不声称解决突然退役',subprocess.check_output([str(binary)],text=True))

    def test_script_syntax_and_isolated_switch(self):
        scripts=[ROOT/'tests'/n for n in ('build_recent_eta_guest.sh','run_recent_eta_guest.sh')]
        subprocess.run(['bash','-n',*map(str,scripts)],check=True)
        wrapper=scripts[1].read_text()
        self.assertIn('ZENFS_RRZC_RECENT_ETA="$recent"',wrapper)
        self.assertIn('ZENFS_RRZC_ETA_OBSERVE=1',wrapper)
        self.assertIn('ZENFS_RRZC_DIRECT=1',wrapper)
        self.assertNotIn('YCSB_RANDOM_SEED',wrapper)

    def test_patch_roundtrip_on_actual_guest_archive(self):
        built=Path('/home/data/xwz/rrzc-baselines/joint-recent-eta-v1-20261020-results/build/joint-recent-eta-v1-20261020')
        if not (built/'before/snapshot.h').is_file():
            self.skipTest('真实客机归档尚不可用，不伪造头文件')
        patch_text=(ROOT/'zenfs-recent-eta.patch').read_text()
        with tempfile.TemporaryDirectory(prefix='rrzc-recent-patch-') as tmp:
            fs=Path(tmp)/'fs';fs.mkdir()
            for name in ('fs_zenfs.cc','snapshot.h','rrzc_direct_zenfs.inc'):
                shutil.copyfile(built/'before'/name,fs/name)
            subprocess.run(['git','apply','--unidiff-zero'],input=patch_text,text=True,cwd=tmp,
                           check=True,capture_output=True)
            for name in ('fs_zenfs.cc','snapshot.h','rrzc_direct_zenfs.inc'):
                self.assertEqual((fs/name).read_bytes(),(built/name).read_bytes())
            subprocess.run(['git','apply','--unidiff-zero','--reverse'],input=patch_text,text=True,cwd=tmp,
                           check=True,capture_output=True)
            for name in ('fs_zenfs.cc','snapshot.h','rrzc_direct_zenfs.inc'):
                self.assertEqual((fs/name).read_bytes(),(built/'before'/name).read_bytes())


if __name__=='__main__':unittest.main()
