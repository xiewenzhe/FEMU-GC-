"""全块接线补丁与构建解析的回归；真实设备结果另行核验。"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from all_blocks_patch import make_patch
from build_all_blocks_guest import compile_command,old_zenfs_extracted

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('/home/data/xwz/rrzc-baselines/joint-dbbench8-locality-v4-20261024-results/源码')
class AllBlocksTest(unittest.TestCase):
    def test_real_source_patch_roundtrip(self):
        if not SOURCE.is_dir():self.skipTest('没有真实部署源码，不能声称接线已验证')
        with tempfile.TemporaryDirectory(prefix='rrzc-all-blocks-patch-') as folder:
            root=Path(folder);(root/'fs').mkdir()
            for n in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):shutil.copyfile(SOURCE/n,root/'fs'/n)
            fs=(SOURCE/'fs_zenfs.cc').read_text();inc=(SOURCE/'rrzc_direct_zenfs.inc').read_text()
            patch=root/'change.patch';patch.write_text(make_patch(fs,inc))
            for args in (['--check'],[],['--reverse','--check'],['--reverse']):
                subprocess.run(['git','apply',*args,str(patch)],cwd=root,check=True)
            for n in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):self.assertEqual((SOURCE/n).read_bytes(),(root/'fs'/n).read_bytes())
    def test_ambiguous_or_old_source_rejected(self):
        with self.assertRaisesRegex(ValueError,'唯一匹配'):make_patch('','')
    def test_native_make_compiler_command(self):
        line='Warning: Don\'t use debug\necho " CC " file;mkdir -p plugin/zenfs/fs && g++ -DTEST=1 -c plugin/zenfs/fs/fs_zenfs.cc -o plugin/zenfs/fs/fs_zenfs.o\n'
        self.assertEqual(compile_command(line)[0],'g++')
        with self.assertRaises(AssertionError):compile_command(line+line)
        with self.assertRaises(AssertionError):compile_command('')
    def test_guest_shell(self):
        subprocess.run(['bash','-n',str(ROOT/'tests/run_all_blocks_guest.sh')],check=True)
    def test_link_map_exact_member(self):
        self.assertFalse(old_zenfs_extracted('librocksdb_debug.a(zonefs_zenfs.o)'))
        self.assertTrue(old_zenfs_extracted('librocksdb_debug.a(fs_zenfs.o)'))
        self.assertTrue(old_zenfs_extracted('librocksdb_debug.a(plugin/zenfs/fs/fs_zenfs.o)'))
    def test_old_core_identity(self):
        import hashlib,json
        identity=SOURCE.parent/'同库身份.json'
        # 同一次旧部署的身份清单不一定放在来源目录；以v9实际运行身份为准。
        identity=Path('/home/data/xwz/rrzc-baselines/joint-dbbench8-rotating-v9-20261024-results/同库身份.json')
        if not identity.is_file():self.skipTest('没有旧版真实软件身份')
        expected=json.loads(identity.read_text())
        for n in ('rrzc_joint_time_selector.h','rrzc_deadline_selector.h','rrzc_space_plan.h',
                  'rrzc_window_credit_selector.h','rrzc_expected_rr_selector.h'):
            self.assertEqual(hashlib.sha256((ROOT/'model'/n).read_bytes()).hexdigest(),expected['plugin/zenfs/fs/'+n])

if __name__=='__main__':unittest.main()
