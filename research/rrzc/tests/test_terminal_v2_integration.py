"""第二版真实父接线与负载补丁验收；不把补丁检查当作设备实验。"""
import shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

class TerminalV2IntegrationTest(unittest.TestCase):
    def test_parent_patch_roundtrip_and_workload(self):
        parent=Path('/home/data/xwz/rrzc-baselines/joint-terminal-v1-20261023-results/build/joint-terminal-v1-20261023')
        time=Path('/home/data/xwz/rrzc-baselines/joint-time-consistent-v1-20261023-results/build/joint-time-consistent-v1-20261023')
        if not parent.is_dir() or not time.is_dir():self.skipTest('缺少实际父源码')
        with tempfile.TemporaryDirectory(prefix='rrzc-terminal-v2-patch-') as folder:
            d=Path(folder);(d/'fs').mkdir()
            for n in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):shutil.copy2(parent/n,d/'fs'/n)
            shutil.copy2(parent/'joint_opportunity_smoke.cc',d/'joint_opportunity_smoke.cc')
            shutil.copy2(time/'run_joint_time_guest.sh',d/'run_joint_time_guest.sh')
            shutil.copy2(ROOT/'tests/joint_followup.h',d/'joint_followup.h')
            before={str(p.relative_to(d)):p.read_bytes() for p in d.rglob('*') if p.is_file()}
            patches=[ROOT/'zenfs-terminal-v2.patch']+[ROOT/'tests'/n for n in
                ('joint-followup-client.patch','terminal-v2-client.patch','terminal-v2-run.patch')]
            for p in patches:
                subprocess.run(['git','apply','--check',str(p)],cwd=d,check=True)
                subprocess.run(['git','apply',str(p)],cwd=d,check=True)
            core=(d/'fs/rrzc_direct_zenfs.inc').read_text();client=(d/'joint_opportunity_smoke.cc').read_text()
            self.assertIn('rrzc_terminal_v2::Select',core);self.assertNotIn('RRZC_TERMINAL_AGING',core)
            self.assertIn('used_capacity_==12*MiB',client);self.assertIn('std::chrono::seconds(8)',client)
            self.assertLess(client.index('留存先验退役：'),client.index('联合维护释放：'))
            self.assertGreater(client.index('留存后续退役：'),client.index('联合维护释放：'))
            subprocess.run(['bash','-n',str(d/'run_joint_time_guest.sh')],check=True)
            for p in reversed(patches):subprocess.run(['git','apply','--reverse',str(p)],cwd=d,check=True)
            self.assertEqual(before,{str(p.relative_to(d)):p.read_bytes() for p in d.rglob('*') if p.is_file()})

    def test_old_entry_rejects_v2(self):
        from analyze_window_credit import audit_scores
        with self.assertRaisesRegex(ValueError,'旧评分入口拒绝边界新核'):
            audit_scores(['边界配置：版本=2，启用=1，'],'window')

    def test_shell(self):
        subprocess.run(['bash','-n',str(ROOT/'tests/build_terminal_v2_guest.sh')],check=True)

if __name__=='__main__':unittest.main()
