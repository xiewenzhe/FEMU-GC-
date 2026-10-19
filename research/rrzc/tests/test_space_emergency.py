"""空间紧急选择与日志独立重算的合成回归，不代表设备结果。"""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from replay_space_emergency import replay


class SpaceTest(unittest.TestCase):
    LOG='''空间紧急回收配置：启用=1，测试
GC 检查：测试
空间紧急候选：Zone=1，有效字节=115198278，容量字节=268435456，覆盖资格=1，预留资格=1，源稳定=1，原门槛资格=0
空间紧急候选：Zone=2，有效字节=7852，容量字节=268435456，覆盖资格=0，预留资格=1，源稳定=1，原门槛资格=1
简单双因素候选：Zone=2，有效字节=7852，容量字节=268435456，覆盖资格=0，空目标资格=1
空间紧急回收决策：等待数=1，正常安全候选存在=0，实际Zone=1，实际有效字节=115198278，原无效比例门槛=58，回收预算字节=1，RR感知改选=0，专用预留限定=1
简单双因素实际集合：实际选中=1，Zone=1
空间紧急回收结果：实际Zone=1，本轮迁移字节=115198278，本轮重置区数=1，迁移成功=1
ZC 统计：轮次 1，本轮迁移 115198278 字节，本轮重置 1 个 Zone
'''
    def test_success_and_full_ledger_retained(self):
        rows,ds=replay(self.LOG.splitlines())
        self.assertEqual(len(ds),1)
        self.assertEqual(ds[0]['本轮迁移字节'],115198278)
        self.assertTrue(any('ZC 统计：' in row for row in rows))
        self.assertFalse(any('简单双因素候选：' in row for row in rows))

    def test_reject_missing_waiter(self):
        with self.assertRaises(ValueError):replay(self.LOG.replace('等待数=1','等待数=0').splitlines())

    def test_reject_normal_candidate_available(self):
        line='简单双因素候选：Zone=3，有效字节=80000000，容量字节=268435456，覆盖资格=1，空目标资格=1\n'
        with self.assertRaises(ValueError):replay(self.LOG.replace('空间紧急回收决策：',line+'空间紧急回收决策：').splitlines())

    def test_reject_unstable_or_missing_reserve(self):
        for before,after in [('源稳定=1','源稳定=0'),('预留资格=1','预留资格=0')]:
            with self.subTest(before=before):
                with self.assertRaises(ValueError):replay(self.LOG.replace(before,after).splitlines())

    def test_reject_original_gate_mislabel(self):
        with self.assertRaises(ValueError):replay(self.LOG.replace('原无效比例门槛=58','原无效比例门槛=57').splitlines())

    def test_reject_missing_reset(self):
        with self.assertRaises(ValueError):replay(self.LOG.replace('本轮重置区数=1','本轮重置区数=0').splitlines())

    def test_reject_rr_model_in_emergency(self):
        with self.assertRaises(ValueError):replay((self.LOG+'独立约束决策：测试\n').splitlines())

    def test_no_emergency_keeps_original_rounds(self):
        rows=['空间紧急回收配置：启用=1，','GC 检查：测试','普通决策']
        self.assertEqual(replay(rows),(rows,[]))


if __name__=='__main__':unittest.main()
