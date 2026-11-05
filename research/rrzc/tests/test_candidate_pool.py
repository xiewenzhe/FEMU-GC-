"""候选范围核、独立资格验收及新入口隔离测试；合成数据不冒充设备结果。"""
import copy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from analyze_candidate_pool import qualification,strict_stats,compare_three,MODES
from analyze_window_credit import audit_scores


class CandidatePoolTest(unittest.TestCase):
    def test_cpp_original_exhaustive_and_safety(self):
        with tempfile.TemporaryDirectory(prefix='rrzc-pool-unit-') as tmp:
            binary=Path(tmp)/'unit'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_candidate_pool.cc'),'-o',str(binary)],check=True)
            self.assertIn('公共安全资格不变',subprocess.check_output([str(binary)],text=True))

    def test_qualification_not_rename_expanded_as_original(self):
        row=dict(有效字节='46',容量字节='100',无效比例='54',原比例门槛='67',
            满区='1',原比例资格='0',扩大='1',实际比例资格='1')
        self.assertTrue(qualification(row,True))
        for key in ('原比例资格','实际比例资格','扩大'):
            bad=row.copy();bad[key]=str(1-int(bad[key]))
            with self.assertRaises(ValueError):qualification(bad,True)
        row.update(扩大='0',实际比例资格='0');self.assertFalse(qualification(row,False))

    def test_strict_counter_no_overdue_or_unaccounted_reads(self):
        row=dict(版本=1,启用=1,历史最高RC=1024,实际NAND读取=1100,计入RC读取=1024,
            自身刷新豁免读取=76,其他块搬迁计数读取=10)
        strict_stats(row)
        for key in ('历史最高RC','实际NAND读取'):
            bad=row.copy();bad[key]+=1
            with self.assertRaises(ValueError):strict_stats(bad)

    def test_three_arm_identity_and_negative_benefit_allowed(self):
        base=dict(场景='合成场景',初态=[dict(RC=928)],请求计划=[(0,0)],
            RR次数=1,ZC次数=1,总复制字节=100,总成功ZoneReset=2)
        arms={mode:copy.deepcopy(base) for mode in MODES}
        arms[MODES[2]]['总复制字节']=120
        self.assertEqual(compare_three(arms)['比较']['同范围评分影响']['总复制字节'],20)
        arms[MODES[2]]['初态'][0]['RC']=929
        with self.assertRaises(ValueError):compare_three(arms)

    def test_scripts_and_client_patch_reversible(self):
        for name in ('build_candidate_pool_guest.sh','run_candidate_pool_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/name)],check=True)
        with tempfile.TemporaryDirectory(prefix='rrzc-pool-client-') as tmp:
            folder=Path(tmp);source=ROOT/'tests/joint_opportunity_smoke.cc'
            shutil.copyfile(source,folder/source.name)
            patch=ROOT/'tests/candidate-pool-client.patch'
            subprocess.run(['git','apply','--unidiff-zero',str(patch)],cwd=folder,check=True)
            text=(folder/source.name).read_text()
            self.assertIn('scenario=="low-yield" ? 120',text)
            self.assertIn('owned->Open(false,true)',text)
            subprocess.run(['git','apply','--unidiff-zero','--reverse',str(patch)],cwd=folder,check=True)
            self.assertEqual((folder/source.name).read_bytes(),source.read_bytes())

    def test_deployed_source_patch_is_reversible(self):
        source=Path('/tmp/rrzc-strict-mod-20261022/fs/fs_zenfs.cc')
        if not source.is_file():self.skipTest('实际严格RR源副本不存在，不伪造接线验收')
        with tempfile.TemporaryDirectory(prefix='rrzc-pool-patch-') as tmp:
            folder=Path(tmp);(folder/'fs').mkdir();target=folder/'fs/fs_zenfs.cc'
            shutil.copyfile(source,target);patch=ROOT/'zenfs-candidate-pool.patch'
            subprocess.run(['git','apply','--unidiff-zero',str(patch)],cwd=folder,check=True)
            text=target.read_text();self.assertIn('candidate_pool.expanded',text)
            self.assertIn('if (!migrate_zones_start.count(c.zone)) continue;',text)
            subprocess.run(['git','apply','--unidiff-zero','--reverse',str(patch)],cwd=folder,check=True)
            self.assertEqual(target.read_bytes(),source.read_bytes())

    def test_old_analysis_rejects_unrequested_pool(self):
        line='d 1 候选范围配置：版本=1，合法=1，扩大=1，GC启动不变=1，满区要求不变=1，正回收要求不变=1，公共安全不变=1，评分公式不变=1'
        with self.assertRaisesRegex(ValueError,'候选范围执行配置不符'):audit_scores([line],'greedy')

if __name__=='__main__':unittest.main()
