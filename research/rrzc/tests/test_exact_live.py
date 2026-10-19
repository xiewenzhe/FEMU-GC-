"""真实判空的合成日志审计回归，不是设备结果。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from analyze_exact_live import inspect
import run_ycsb_exact_live_host as run

class ExactLiveTest(unittest.TestCase):
    LOG='''真实有效字节判空配置：启用=1，其他=示例
GC 检查：空闲比例=6%，启动水平=20%
ZC 候选：Zone 6442450944，无效比例 100%，有效数据 2302557 字节，选中 1
简单双因素实际集合：实际选中=1，Zone=6442450944
ZC 统计：轮次 1，本轮迁移 2302557 字节，本轮重置 1 个 Zone
'''
    def test_tiny_zone_is_actually_collected(self):
        r=inspect(self.LOG.splitlines());self.assertEqual(r['舍入100但非零有效量候选次数'],1)
        self.assertEqual(r['实际清理记录'][0]['源区重置数'],1)
    def test_reject_rounded_empty_exclusion(self):
        with self.assertRaises(ValueError):inspect(self.LOG.replace('选中 1','选中 0').splitlines())
    def test_reject_gc_threshold_change(self):
        with self.assertRaises(ValueError):inspect(self.LOG.replace('启动水平=20%','启动水平=25%').splitlines())
    def test_same_workload_and_stopped_parent(self):
        self.assertEqual(run.PARENT.name,'ycsb-empty-reserve-20261017-build.qcow2')
        text=(run.TESTS/'run_ycsb_exact_live_guest.sh').read_text()
        self.assertIn('ZENFS_RRZC_EXACT_LIVE=1',text)
        self.assertIn('ZENFS_RRZC_RECLAIM_BYTES=201326592',text)
        self.assertIn('workloada-zns-nowal.properties',text)

if __name__=='__main__':unittest.main()
