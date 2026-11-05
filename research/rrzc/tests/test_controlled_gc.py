"""仅受控实验改变GC检查时点；确认原资格与选区源码保持逐字不变。"""
from pathlib import Path
import unittest
from controlled_gc_source import patched


class ControlledGcTest(unittest.TestCase):
    def test_only_worker_wait_and_ack_are_added(self):
        path=Path('/home/data/xwz/rrzc-baselines/joint-controlled-input-v1-20261026-results/源码/修复后-fs_zenfs.cc')
        if not path.is_file():self.skipTest('归档ZenFS来源不存在')
        native=path.read_text();result=patched(native)
        begin='    if (!superblock_->IsGCEnabled()) continue;'
        self.assertEqual(native.split(begin,1)[1],result.split(begin,1)[1])
        self.assertIn('受控GC检查点开始：批次=',result)
        self.assertIn('受控GC紧急检查：上一完成批次=',result)
        self.assertIn('zbd_->GetAllocationWaiters()',result)
        self.assertEqual(result.count('GateAck gate_ack'),1)
        self.assertEqual(result.count('free_percent > GC_START_LEVEL'),native.count('free_percent > GC_START_LEVEL'))


if __name__=='__main__':unittest.main()
