"""用明确的合成单测事件验证归因脚本拒绝错误证据；不生成实验收益。"""
from pathlib import Path
import sys
import struct
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_placement_survivor import MODE, audit_history
from run_placement_survivor_pair_host import PAIR_LABEL, check_label, wrapper


class PairAuditTest(unittest.TestCase):
    def fixture(self, root, first_state=0, pool_after_gate=False):
        records, copies = [], []
        def emit(kind, *args):
            seq = len(records) + 1
            row = [seq,seq,1,kind,1,10,seq,*args]
            records.append(struct.pack('<16Q', *(row + [0] * (16-len(row)))))
            return seq
        emit(2,50,11,1,1,0,0,4096)
        emit(9,2,0,4096,4)
        emit(12,100,0,8192,0,0,2,1)
        event = emit(13,20,90,90,100,100,4096,0)
        copies.append(dict(事件=event,有效复制字节=4096,映射结局='提交'))
        emit(14,20,1)
        emit(9,2,0,4096,4)
        if pool_after_gate: emit(11,200,0,8192,0,0,2,1)
        emit(12,200,0,8192,0,0,2,1)
        event = emit(13,21,100,100,200,200,4096,0)
        copies.append(dict(事件=event,有效复制字节=4096,映射结局='提交'))
        emit(14,21,1)
        (root/'组成.bin').write_bytes(b'RRZCCP01'+struct.pack('<3Q',len(records),0,1)+b''.join(records))
        (root/'run.zenfs.log').write_text(
            f'W历史准入评估：对象=10，历史状态={first_state}，所需字节=4096\n'
            'W历史准入评估：对象=10，历史状态=2，所需字节=4096\n'
            'W历史准入回退：模式=G2_WSURVIVOR，对象=10，类别=2，需要字节=4096\n')
        return dict(组成=dict(复制明细=copies))

    def test_gate_has_prior_commit_and_actual_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);result=audit_history(root,self.fixture(root),MODE)
            self.assertEqual(result['拒绝次数'],1)
            self.assertIsNotNone(result['逐次'][1]['本会话先前成功提交事件'])
            self.assertEqual(result['逐次'][1]['实际目标'],200)

    def test_no_prior_commit_or_pool_after_gate_rejected(self):
        for state,pool in [(2,False),(0,True)]:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);report=self.fixture(root,state,pool)
                with self.assertRaises(ValueError): audit_history(root,report,MODE)

    def test_short_paths_and_wrapper_only_change_modes(self):
        check_label(PAIR_LABEL)
        with self.assertRaises(ValueError): check_label(PAIR_LABEL+'-too-long-for-the-control-socket')
        from placement_business_source import wrapper as old_wrapper
        source=Path('/home/data/xwz/rrzc-baselines/joint-controlled-end-to-end-v1-20261105-results/源码/run_pool_business_guest.sh').read_text()
        self.assertEqual(wrapper(source).replace('( $1 == G2 || $1 == G2_WSURVIVOR )',
            '( $1 == G0 || $1 == G1 || $1 == G2 )'),old_wrapper(source))


if __name__=='__main__': unittest.main()
