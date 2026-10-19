"""合成尾部日志回归：不能把Finish或丢弃空间当成重复搬运。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_tail_stream import ledger, CAPACITY

MIB = 1 << 20
A, B, S = CAPACITY * 3, CAPACITY * 4, CAPACITY * 5


def acquire(zone=A):
    return f"迁移预留取得空区：Zone={zone}"


def use(zone, length, remaining):
    return f"迁移使用专用预留：Zone={zone}，需要={length}，剩余={remaining}"


def copy(fid, source, target, length, offset=None):
    if offset is None:
        offset = target
    return (f"V1后续迁移复制：文件编号={fid}，文件名=/合成/{fid}.sst，"
            f"源Zone={source}，目标Zone={target}，目标片段={offset}，"
            f"有效字节={length}，复制成功=1")


def commit(fid):
    return f"V1后续映射提交：文件编号={fid}，成功=1"


def seed(length=240*MIB):
    return [acquire(), use(A, length, CAPACITY), copy(1,S,A,length), commit(1)]


def replacement():
    return f"迁移预留替换：旧Zone={A}，新Zone={B}"


def reset(zone=A):
    return f"Zone 重置诊断：Zone={zone}，状态=成功，主机代际=0/1，观察序号=2"


class TailAnalysis(unittest.TestCase):
    def test_enough_tail_without_finish(self):
        lines=seed(224*MIB)+[use(A,32*MIB,32*MIB),copy(2,S,A,32*MIB,A+224*MIB),commit(2)]
        result=ledger(lines)
        self.assertEqual(result["预留替换"],[])
        self.assertEqual(result["重复文件复制字节"],0)
        self.assertEqual(result["预留申请"][-1]["实际复制字节"],32*MIB)

    def test_insufficient_tail_discard_is_not_copy(self):
        result=ledger(seed()+[replacement(),use(B,32*MIB,CAPACITY),copy(2,S,B,32*MIB),commit(2)])
        event=result["预留替换"][0]
        self.assertEqual(event["丢弃尾部字节"],16*MIB)
        self.assertEqual(event["当时已追踪有效字节"],240*MIB)
        self.assertEqual(event["旧区有效文件后续复制字节"],0)
        self.assertEqual(result["重复文件复制字节"],0)

    def test_later_same_file_copy_is_separate_event(self):
        result=ledger(seed()+[replacement(),use(B,240*MIB,CAPACITY),copy(1,A,B,240*MIB),commit(1)])
        self.assertEqual(result["预留替换"][0]["旧区有效文件后续复制字节"],240*MIB)
        self.assertEqual(result["重复文件复制字节"],240*MIB)

    def test_natural_full_rollover_not_premature_finish(self):
        result=ledger(seed(CAPACITY)+[acquire(B),use(B,CAPACITY,CAPACITY),copy(1,A,B,CAPACITY),commit(1)])
        self.assertEqual(result["预留替换"],[])
        self.assertEqual(len(result["自然写满后取得新预留"]),1)
        self.assertEqual(result["重复文件复制字节"],CAPACITY)
        self.assertEqual(result["可确认丢弃尾部字节"],0)

    def test_reset_reuse_not_attributed_to_old_files(self):
        result=ledger(seed()+[replacement(),reset(),copy(2,A,B,32*MIB),commit(2)])
        self.assertEqual(result["预留替换"][0]["旧区有效文件后续复制"],[])
        self.assertEqual(result["实际复制"][-1]["源代际"],1)

    def test_retired_file_not_in_replacement_valid_data(self):
        result=ledger(seed()+["V1后续文件退役：文件编号=1",replacement()])
        self.assertEqual(result["预留替换"][0]["当时已追踪有效字节"],0)

    def test_rr_source_finish_not_reserve_replacement(self):
        result=ledger(seed()+[f"RR 封闭专用预留源：Zone={A}，封闭剩余={16*MIB} 字节，不归还普通活跃令牌"])
        self.assertEqual(result["预留替换"],[])
        self.assertEqual(result["预留源RR封闭"][0]["封闭尾部字节"],16*MIB)

    def test_alternative_target_does_not_change_reserve_tail(self):
        result=ledger(seed()+[copy(2,S,B,32*MIB),commit(2),
            f"成本预留观察：可知=1，存在=1，Zone={A}，剩余字节={16*MIB}，有效字节={240*MIB}"])
        self.assertEqual(result["预留替换"],[])

    def test_unobserved_reserve_identity_rejected(self):
        with self.assertRaises(ValueError): ledger([replacement()])

    def test_wrong_write_pointer_rejected(self):
        with self.assertRaises(ValueError):
            ledger(seed(224*MIB)+[use(A,32*MIB,32*MIB),copy(2,S,A,32*MIB),commit(2)])

    def test_missing_commit_rejected(self):
        with self.assertRaises(ValueError): ledger(seed()[:-1])

    def test_pending_request_rejected(self):
        with self.assertRaises(ValueError): ledger([acquire(),use(A,32*MIB,CAPACITY)])

    def test_reset_generation_gap_rejected(self):
        with self.assertRaises(ValueError):
            ledger([f"Zone 重置诊断：Zone={A}，状态=成功，主机代际=0/2，观察序号=4"])

    def test_wrong_repeated_source_rejected(self):
        with self.assertRaises(ValueError): ledger(seed()+[copy(1,S,B,240*MIB),commit(1)])

    def test_cost_snapshot_tail_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            ledger(seed()+[f"成本预留观察：可知=1，存在=1，Zone={A}，剩余字节={32*MIB}，有效字节={240*MIB}"])

    def test_cost_snapshot_valid_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            ledger(seed()+[f"成本预留观察：可知=1，存在=1，Zone={A}，剩余字节={16*MIB}，有效字节=0"])


if __name__ == "__main__": unittest.main()
