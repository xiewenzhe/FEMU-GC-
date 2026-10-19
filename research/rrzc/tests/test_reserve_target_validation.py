"""基础验证入口与进度监视的回归；示例日志不代表设备结果。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import probe_ycsb_progress as probe
import run_ycsb_reserve_target_host as run
from analyze_reserve_target_validation import inspect

class ProgressTest(unittest.TestCase):
    def test_initial_and_stage_transition(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(probe,'ROOT',Path(tmp)):
            self.assertIsNone(probe.progress()['操作数'])
            log=Path(tmp)/'ycsb.log'
            log.write_text('2026-10-17 05:57:07 10 sec: 655360 operations;\n')
            self.assertEqual(probe.progress()['阶段'],'装载')
            log.write_text(log.read_text()+'Load runtime(sec): 50\n')
            self.assertEqual(probe.progress()['阶段'],'装载后等待')
            self.assertIsNone(probe.progress()['操作数'])
            log.write_text(log.read_text()+'2026-10-17 05:58:17 0 sec: 0 operations;\n')
            self.assertEqual(probe.progress()['阶段'],'业务')
            self.assertEqual(probe.progress()['操作数'],0)
    def test_background_error(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(probe,'ROOT',Path(tmp)):
            root=Path(tmp);(root/'ycsb.log').write_text('2026-10-17 05:58:27 10 sec: 39999 operations;\n')
            (root/'aux/rrzc-ycsb').mkdir(parents=True)
            (root/'aux/rrzc-ycsb/LOG').write_text('示例：Background IO error IO error: No space left on device: Zone allocation failure\n')
            self.assertTrue(probe.progress()['错误'])
            self.assertEqual(probe.progress()['操作数'],39999)
    def test_single_greedy_new_parent_and_port(self):
        self.assertEqual(run.PARENT.name,'ycsb-nowal-20261017-build.qcow2')
        self.assertEqual(run.PORT,'8095')
        self.assertNotEqual(run.PARENT,run.BUILD_IMAGE)
        script=(run.TESTS/'run_ycsb_reserve_target_guest.sh').read_text()
        self.assertIn('$1 == greedy',script)
        self.assertIn('ZENFS_RRZC_RESERVE_TARGET=1',script)
        self.assertIn('ZENFS_RRZC_RECLAIM_BYTES=201326592',script)
    def test_no_device_allocator_or_rr_patch(self):
        text=(run.PROJECT/'research/rrzc/zenfs-reserve-target.patch').read_text()
        self.assertNotIn('zbd_zenfs.cc',text)
        self.assertNotIn('zns_rr_threshold',text)
        self.assertIn('simple_enabled && !simple_weighted',text)

class GateReplayTest(unittest.TestCase):
    # 以下是示例日志，仅用于边界回归。
    LOG='''预留目标资格配置：启用=1，其他=示例
GC 检查：示例
预留目标资格候选：Zone=32768，空目标可用=0，预留可用=1，生效目标资格=1，覆盖字节=8192，片段数=1，物理上界字节=12288，预留Zone=4096，预留剩余字节=12288，预留容量字节=16384，复核稳定=1
简单双因素候选：Zone=32768，有效字节=8192，覆盖资格=1，空目标资格=1
简单双因素实际集合：模式=valid，原合格区=1，实际选中=1，Zone=32768
ZC 统计：轮次 1，本轮迁移 8192 字节，本轮重置 1 个 Zone
'''
    def test_associate_actual_zc(self):
        out=inspect(self.LOG)
        self.assertEqual(out['无空目标时选中且重置成功次数'],1)
        self.assertEqual(out['无空目标时选中的ZC轮次'][0]['实际复制字节'],8192)
    def test_not_completed_when_only_selected(self):
        out=inspect(self.LOG.replace('本轮重置 1 个','本轮重置 0 个'))
        self.assertEqual(out['无空目标时选中且重置成功次数'],0)
    def test_reject_missing_physical_space(self):
        with self.assertRaises(ValueError):inspect(self.LOG.replace('预留剩余字节=12288','预留剩余字节=8192'))
    def test_reject_stale_qualification(self):
        with self.assertRaises(ValueError):inspect(self.LOG.replace('生效目标资格=1','生效目标资格=0'))

if __name__=='__main__':unittest.main()
