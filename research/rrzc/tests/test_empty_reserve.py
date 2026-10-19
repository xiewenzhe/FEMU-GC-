"""完整空预留的合成审计回归，不代表真实设备结果。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from analyze_empty_reserve import inspect
import run_ycsb_empty_reserve_host as run

class EmptyReserveTest(unittest.TestCase):
    LINE='公共完整空预留周转：新Zone=1073741824，新剩余字节=268435456，旧存在=1，旧Zone=805306368，旧剩余字节=8388608，保留旧尾部=1，普通活跃令牌新增=1，牺牲尾部字节=0，新区独占保留=1'
    def test_preserved(self):
        r=inspect([self.LINE]);self.assertEqual(r['完整空预留周转次数'],1);self.assertEqual(r['牺牲尾部字节'],0)
    def test_record_finish_cost(self):
        line=self.LINE.replace('保留旧尾部=1','保留旧尾部=0').replace('普通活跃令牌新增=1','普通活跃令牌新增=0').replace('牺牲尾部字节=0','牺牲尾部字节=8388608')
        self.assertEqual(inspect([line])['牺牲尾部MiB'],8)
    def test_reject_partial_new_reserve(self):
        with self.assertRaises(ValueError):inspect([self.LINE.replace('新剩余字节=268435456','新剩余字节=128')])
    def test_runtime_keeps_original_workload_and_disables_failed_exchange(self):
        self.assertEqual(run.PARENT.name,'ycsb-reserve-exchange-20261017-build.qcow2')
        text=(run.TESTS/'run_ycsb_empty_reserve_guest.sh').read_text()
        self.assertIn('ZENFS_RRZC_EMPTY_RESERVE=1',text)
        self.assertIn('ZENFS_RRZC_RESERVE_EXCHANGE=0',text)
        self.assertIn('ZENFS_RRZC_RECLAIM_BYTES=201326592',text)

if __name__=='__main__':unittest.main()
