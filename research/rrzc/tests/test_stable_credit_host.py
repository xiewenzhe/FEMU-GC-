"""新受控入口及拒绝边界；合成日志只用于单元测试，不冒充设备结果。"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from analyze_stable_credit import audit_scores
import run_stable_credit_host as host


class StableHostTest(unittest.TestCase):
    def test_scripts_and_unchanged_business_source(self):
        for name in ('build_stable_credit_guest.sh','run_stable_credit_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/name)],check=True)
        wrapper=(ROOT/'tests/run_stable_credit_guest.sh').read_text()
        self.assertIn('ZENFS_RRZC_DIRECT=1',wrapper)
        self.assertIn('ZENFS_RRZC_PAIR_GREEDY="$greedy"',wrapper)
        self.assertIn('ZENFS_RRZC_STABLE_CREDIT="$stable"',wrapper)
        self.assertIn('ZENFS_RR_ENABLE=0',wrapper)
        self.assertIn('ZENFS_RR_ENABLE=1',wrapper)
        self.assertNotIn('GC_START',wrapper)
        self.assertNotIn('reset-zone',wrapper)
        self.assertIn('joint_opportunity_smoke.cc',[p.name for p in host.FILES])

    def test_layered_patch_roundtrip_keeps_common_safety(self):
        built=Path('/home/data/xwz/rrzc-baselines/joint-recent-eta-v1-20261020-results/build/joint-recent-eta-v1-20261020')
        if not (built/'fs_zenfs.cc').exists():self.skipTest('真实源码归档不可用，不伪造补丁成功')
        patches=[(ROOT/name).read_text() for name in ('zenfs-stable-credit.patch','zenfs-stable-greedy-pair.patch')]
        with tempfile.TemporaryDirectory() as tmp:
            fs=Path(tmp)/'fs';fs.mkdir()
            for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):shutil.copyfile(built/name,fs/name)
            for patch in patches:
                subprocess.run(['git','apply','--unidiff-zero'],input=patch,text=True,cwd=tmp,check=True,capture_output=True)
            code=(fs/'rrzc_direct_zenfs.inc').read_text()
            self.assertIn('pair_greedy ? rrzc_direct::Rank(candidates,{}, {}',code)
            tail='  const auto reserve=zbd_->GetMigrationReserveObservation();'
            self.assertEqual(code.split(tail)[1],(built/'rrzc_direct_zenfs.inc').read_text().split(tail)[1])
            for patch in reversed(patches):
                subprocess.run(['git','apply','--unidiff-zero','--reverse'],input=patch,text=True,cwd=tmp,check=True,capture_output=True)
            for name in ('fs_zenfs.cc','rrzc_direct_zenfs.inc'):
                self.assertEqual((fs/name).read_bytes(),(built/name).read_bytes())

    def test_reject_missing_actual_mode_or_wrong_zeros(self):
        with self.assertRaises(ValueError):audit_scores([],'greedy')
        with self.assertRaises(ValueError):audit_scores([
            '稳定收益配对模式：轮次=1，Greedy=0，稳定模型=1，共用安全复核=1'],'greedy')
        lines=['稳定收益配对模式：轮次=1，Greedy=1，稳定模型=0，共用安全复核=1',
               'V1影子候选：轮次=1，Zone=0，原候选资格=1，统计准入=0',
               '统一留存：轮次=1，Zone=0，可知=0，十秒减少=0，三十秒减少=0，十秒间隔=0，三十秒间隔=0',
               f'统一成本：轮次=1，Zone=0，有效=4，回收=124，风险可知=0，ETA={2**64-1}，预计留存=0，RR抵扣=0，净复制=4']
        self.assertEqual(audit_scores(lines,'greedy')[0]['净复制'],4)
        lines[-1]=lines[-1].replace('RR抵扣=0','RR抵扣=1')
        with self.assertRaises(ValueError):audit_scores(lines,'greedy')

    def test_stable_zero_statistics_and_chinese_reason(self):
        lines=['稳定收益配对模式：轮次=1，Greedy=0，稳定模型=1，共用安全复核=1',
               'V1影子候选：轮次=1，Zone=0，原候选资格=1，统计准入=0',
               '统一留存：轮次=1，Zone=0，可知=0，十秒减少=0，三十秒减少=0，十秒间隔=0，三十秒间隔=0',
               f'统一成本：轮次=1，Zone=0，有效=4，回收=124，风险可知=0，ETA={2**64-1}，预计留存=0，RR抵扣=0，净复制=4',
               f'稳定收益成本：轮次=1，Zone=0，风险可知=0，稳定ETA={2**64-1}，预计留存=0，RR抵扣=0，净复制=4，短窗ETA={2**64-1}，十秒ETA={2**64-1}，原因=统计未知，不猜测']
        self.assertEqual(audit_scores(lines,'stable')[0]['RR抵扣'],0)
        lines[-1]=lines[-1].replace('净复制=4','净复制=3')
        with self.assertRaises(ValueError):audit_scores(lines,'stable')


if __name__=='__main__':unittest.main()
