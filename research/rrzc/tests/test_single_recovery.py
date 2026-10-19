"""公共单区恢复的合成审计回归，不代表设备实验结果。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from replay_single_recovery import replay
import run_ycsb_single_recovery_host as run
import run_ycsb_reserve_exchange_host as exchange

class RecoveryTest(unittest.TestCase):
    LOG='''公共单区恢复配置：启用=1，其他=示例
GC 检查：示例
简单双因素候选：Zone=1，有效字节=83886080，容量字节=268435456，覆盖资格=1，空目标资格=1
简单双因素候选：Zone=2，有效字节=94371840，容量字节=268435456，覆盖资格=1，空目标资格=1
公共单区恢复决策：存在=1，实际Zone=1，实际有效字节=83886080，原回收预算字节=201326592，应急回收预算字节=1，RR感知改选=0
简单双因素实际集合：模式=valid，实际选中=1，Zone=1
公共单区恢复维护结果：实际Zone=1，本轮迁移字节=83886080，本轮重置区数=1，迁移成功=1，状态=OK
ZC 统计：轮次 1，本轮迁移 83886080 字节，本轮重置 1 个 Zone
'''
    def test_choose_one_and_retain_raw_migration(self):
        lines,ds=replay(self.LOG.splitlines())
        self.assertEqual(len(ds),1)
        self.assertFalse(any('简单双因素候选：' in s for s in lines))
        self.assertTrue(any('ZC 统计：' in s for s in lines))
    def test_reject_batch(self):
        with self.assertRaises(ValueError):replay(self.LOG.replace('实际选中=1','实际选中=2').splitlines())
    def test_reject_wrong_greedy(self):
        with self.assertRaises(ValueError):replay(self.LOG.replace('实际Zone=1','实际Zone=2').splitlines())
    def test_reject_reset_failure(self):
        with self.assertRaises(ValueError):replay(self.LOG.replace('本轮重置区数=1','本轮重置区数=0').splitlines())
    def test_reject_unnecessary_emergency(self):
        with self.assertRaises(ValueError):replay(self.LOG.replace('83886080','33554432').splitlines())
    def test_non_sst_budget_does_not_block_safe_emergency(self):
        line='简单双因素候选：Zone=0，有效字节=7852，容量字节=268435456，覆盖资格=0，空目标资格=1\n'
        source=self.LOG.replace('公共单区恢复决策：',line+'公共单区恢复决策：')
        self.assertEqual(replay(source.splitlines())[1][0]['实际Zone'],'1')
    def test_keep_old_path(self):
        lines,ds=replay(['公共单区恢复配置：启用=1，','GC 检查：示例','普通轮'])
        self.assertEqual(ds,[]);self.assertEqual(lines[-1],'普通轮')
    def test_isolation(self):
        self.assertEqual(run.PORT,'8096')
        self.assertEqual(run.PARENT.name,'ycsb-reserve-target-20261017-build.qcow2')
        text=(run.TESTS/'run_ycsb_single_recovery_guest.sh').read_text()
        self.assertIn('ZENFS_RRZC_RECLAIM_BYTES=201326592',text)
        self.assertIn('ZENFS_RRZC_SINGLE_RECOVERY=1',text)
        self.assertIn('$1 == greedy',text)
    def test_physical_empty_guard_reaches_effective_qualification(self):
        text=(run.PROJECT/'research/rrzc/zenfs-single-recovery.patch').read_text()
        self.assertIn('+          c.target_available=empty_ok || reserve_ok;',text)
    def test_exchange_uses_stopped_build_and_explicit_flag(self):
        self.assertEqual(exchange.PARENT.name,'ycsb-single-recovery-20261017-r2-build.qcow2')
        script=(exchange.TESTS/'run_ycsb_reserve_exchange_guest.sh').read_text()
        self.assertIn('ZENFS_RRZC_RESERVE_EXCHANGE=1',script)
        self.assertIn('ZENFS_RRZC_RECLAIM_BYTES=201326592',script)
    def test_exchange_does_not_finish_reset_or_copy(self):
        source=(run.PROJECT/'research/rrzc/zenfs-reserve-exchange.patch').read_text()
        body=source.split('IOStatus ZonedBlockDevice::ExchangeMigrationReserve(',1)[1].split('rrzc_cost::ReserveObservation',1)[0]
        self.assertNotIn('->Finish(',body);self.assertNotIn('->Reset(',body)
        self.assertNotIn('MigrateData(',body)
        self.assertNotIn('PutActiveIOZoneToken(',body)
        self.assertNotIn('GetActiveIOZoneTokenIfAvailable(',body)

if __name__=='__main__':unittest.main()
