"""内容隔离和RR待维护验收边界；合成输入均明确只作单元测试。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import shutil
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rrzc_rr_health import inspect
ROOT=Path(__file__).resolve().parents[1]

def frame(t,rc=4100,generation=0,known=1,valid=100):
    return (f'RR风险帧：时点纳秒={t*10**9}，区数=1\n'
        f'RR风险状态：时点纳秒={t*10**9}，Zone=1，可信={known}，代际={generation}，序号={generation*2}，有效={valid}，最大RC={rc}，阈值=4096，达阈值块=1，覆盖=100，含非SST=0\n')
CONFIG='公共内容隔离配置：启用=1，\n'

class HealthTest(unittest.TestCase):
    def test_business_exit_only_disables_live_freshness_not_history_audit(self):
        import probe_ycsb_progress as probe
        with tempfile.TemporaryDirectory(prefix='rrzc-health-exit-') as tmp:
            root=Path(tmp)
            self.assertIsInstance(probe.live_check_time(root),int)
            (root/'business.exit').touch()
            self.assertIsNone(probe.live_check_time(root))
            bad=CONFIG+''.join(frame(t) for t in (1,11,21,31,41))
            self.assertFalse(inspect(bad,strict=False,now_ns=probe.live_check_time(root))['验收通过'])

    def test_actual_patch_applies_and_reverses(self):
        before=ROOT.parents[1]/'build-femu/rrzc/content-guard-before/fs'
        if not before.is_dir():self.skipTest('没有真实冻结父源码')
        with tempfile.TemporaryDirectory(prefix='rrzc-content-patch-') as tmp:
            out=Path(tmp)/'fs';out.mkdir()
            for name in ('fs_zenfs.cc','zbd_zenfs.cc','zbd_zenfs.h','io_zenfs.cc'):shutil.copyfile(before/name,out/name)
            patch=(ROOT/'zenfs-content-guard.patch').read_text()
            for extra in ([],['--reverse']):
                subprocess.run(['git','apply',*extra],input=patch,cwd=tmp,text=True,capture_output=True,check=True)
            for p in out.iterdir():self.assertEqual(p.read_bytes(),(before/p.name).read_bytes())
    def test_compile_classes(self):
        with tempfile.TemporaryDirectory(prefix='rrzc-content-unit-') as d:
            exe=Path(d)/'unit'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_zone_content.cc'),'-o',str(exe)],check=True,capture_output=True)
            subprocess.run([str(exe)],check=True)
    def test_persistent_unserviced_risk_is_rejected(self):
        r=inspect(CONFIG+''.join(frame(t) for t in (1,11,21,31,41)))
        self.assertFalse(r['验收通过']);self.assertEqual(r['最长已观察超阈值等待秒'],40)
    def test_reset_zero_and_retirement_end_same_epoch_wait(self):
        for extra in (frame(21,rc=0),frame(21,generation=1),frame(21,valid=0)):
            r=inspect(CONFIG+frame(1)+frame(11)+extra+frame(31,generation=1))
            self.assertTrue(r['验收通过'],r)
    def test_unknown_does_not_erase_known_risk(self):
        r=inspect(CONFIG+frame(1)+''.join(frame(t,known=0) for t in (11,21,31,41)))
        self.assertFalse(r['验收通过'])
    def test_mixed_allocation_and_missing_frames_fail(self):
        bad='公共内容分配：阶段=迁移，Zone=1，原类别=2，请求类别=1，实际类别=1\n'
        self.assertFalse(inspect(CONFIG+frame(1)+bad)['验收通过'])
        self.assertFalse(inspect('')['验收通过'])
        self.assertFalse(inspect(CONFIG+frame(1)+frame(41))['验收通过'])
        self.assertFalse(inspect(CONFIG+frame(1),strict=False,now_ns=32*10**9)['验收通过'])

    def test_old_real_baseline_no_longer_counts_as_healthy(self):
        old=Path('/home/data/xwz/rrzc-baselines/direct-spare-v4-20261020-results/greedy/20261017/zenfs.run.log')
        if not old.is_file():self.skipTest('没有真实旧基线日志')
        r=inspect(old.read_text());self.assertFalse(r['验收通过'])
        self.assertEqual(r['分区延期观察数'],{'1610612736':98})

if __name__=='__main__':unittest.main()
