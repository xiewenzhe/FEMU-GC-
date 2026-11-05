"""后续压力测试的分账与真实父程序补丁回归；示例日志仅用于单元测试。"""
import shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from analyze_joint_followup import ledger,unique,analyze_arm

class FollowupTest(unittest.TestCase):
    def test_example_ledger(self):
        lines=['V1后续迁移复制：有效字节=4194304，复制成功=1',
            'ZC 统计：轮次 1，本轮迁移 4194304 字节，本轮重置 1 个 Zone',
            'Zone 重置诊断：Zone=128，状态=成功，主机代际=0/1，观察序号=2，',
            'V1后续迁移复制：有效字节=5242880，复制成功=1',
            'RR 统计：尝试 1 次，本次迁移 5242880 字节，本次重置 1 个 Zone，累计迁移 5242880 字节']
        result=ledger(lines)
        self.assertEqual(result['总复制字节'],9<<20)
        self.assertEqual((result['ZC次数'],result['RR次数'],result['总成功ZoneReset']),(1,1,1))
        with self.assertRaisesRegex(ValueError,'账目不闭合'):ledger(lines[1:])
        with self.assertRaisesRegex(ValueError,'代际错误'):
            ledger(['Zone 重置诊断：Zone=128，状态=成功，主机代际=0/2，观察序号=2，'])

    def test_phase_markers(self):
        self.assertEqual(unique(['a','后续空间压力开始：'],'后续空间压力开始：'),1)
        for rows in ([],['tag','tag']):
            with self.assertRaisesRegex(ValueError,'缺失或重复'):unique(rows,'tag')

    def test_real_parent_patch_roundtrip(self):
        source=Path('/home/data/xwz/rrzc-baselines/joint-time-consistent-v1-20261023-results/build/joint-time-consistent-v1-20261023/joint_opportunity_smoke.cc')
        if not source.is_file():self.skipTest('缺少真实父客户端，不伪造接线通过')
        patch=ROOT/'tests/joint-followup-client.patch'
        with tempfile.TemporaryDirectory(prefix='rrzc-followup-patch-') as folder:
            target=Path(folder)/source.name;shutil.copy2(source,target)
            subprocess.run(['git','apply','--check',str(patch)],cwd=folder,check=True)
            subprocess.run(['git','apply',str(patch)],cwd=folder,check=True)
            self.assertIn('followup_run(*fs,*d,logger,out)',target.read_text())
            subprocess.run(['git','apply','--reverse',str(patch)],cwd=folder,check=True)
            self.assertEqual(target.read_bytes(),source.read_bytes())

    def test_old_data_cannot_count_as_followup(self):
        source=Path('/home/data/xwz/rrzc-baselines/joint-time-consistent-v1-20261023-results/opportunity-space/opportunity')
        if not source.is_dir():self.skipTest('缺少真实旧数据')
        with self.assertRaisesRegex(ValueError,'后续阶段边界缺失'):analyze_arm(source,'space')

    def test_shell_syntax(self):
        subprocess.run(['bash','-n',str(ROOT/'tests/build_joint_followup_guest.sh')],check=True)

if __name__=='__main__':unittest.main()
