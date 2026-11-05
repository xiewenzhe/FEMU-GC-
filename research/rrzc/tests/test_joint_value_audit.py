"""合成日志只验证物理片段关联边界，不生成实验性能结果。"""
import unittest
from analyze_joint_value import parse,ZONE


def sample(second_size=4,reset_target=False,failed_commit=False):
    events=[]
    def add(body):events.append(f'2026/10/28-00:00:{len(events):02d}.000000 7 '+body)
    add('时序成本：轮次=1，Zone=0，有效=4，安全=1，ETA=10，RR复制=3')
    add('时序方案决策：轮次=1，实际存在=1，实际Zone=0')
    add(f'V1后续迁移复制：文件编号=1，文件名=/1.sst，源Zone=0，源片段=0，目标Zone={ZONE}，目标片段={ZONE}，有效字节=4，复制成功=1')
    add('V1后续映射提交：文件编号=1，成功=1')
    add('Zone 重置诊断：Zone=0，状态=成功，主机代际=0/1')
    add('ZC 统计：轮次 1，本轮迁移 4 字节')
    if reset_target:add(f'Zone 重置诊断：Zone={ZONE}，状态=成功，主机代际=0/1')
    add(f'RR 开始：Zone {ZONE}，达阈值物理块 1，有效数据 {second_size} 字节，封闭剩余容量 0')
    add(f'V1后续迁移复制：文件编号=1，文件名=/1.sst，源Zone={ZONE}，源片段={ZONE}，目标Zone={2*ZONE}，目标片段={2*ZONE}，有效字节={second_size}，复制成功=1')
    add(f'V1后续映射提交：文件编号=1，成功={int(not failed_commit)}')
    add(f'Zone 重置诊断：Zone={ZONE}，状态=成功，主机代际={1 if reset_target else 0}/{2 if reset_target else 1}')
    add(f'RR 统计：尝试 1 次，本次迁移 {second_size} 字节')
    return events


class JointValueAuditTest(unittest.TestCase):
    def test_committed_same_extent_is_counted(self):
        result=parse(sample())
        self.assertEqual(result['片段重复字节分布'],{'ZC→RR':4})
        self.assertEqual(result['复制'][1]['前次复制'],1)

    def test_reset_prevents_same_address_false_match(self):
        self.assertEqual(parse(sample(reset_target=True))['片段重复复制字节'],0)

    def test_partial_overlap_is_not_claimed_as_exact_duplicate(self):
        self.assertEqual(parse(sample(second_size=2))['片段重复复制字节'],0)

    def test_failed_mapping_is_rejected(self):
        with self.assertRaises(AssertionError):parse(sample(failed_commit=True))


if __name__=='__main__':unittest.main()
