"""业务入口差异和Greedy审计回归；不启动客机、不生成业务收益。"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from placement_business_source import wrapper
from placement_ablation_config import OLD_KEYS
from run_placement_ablation_host import OLD
from analyze_placement_business import greedy_decisions


class BusinessTest(unittest.TestCase):
    def test_wrapper_only_changes_mode_routing_and_observation(self):
        old=(OLD/'源码/run_pool_business_guest.sh').read_text();new=wrapper(old)
        for key in OLD_KEYS:self.assertNotIn(key+'=',new)
        self.assertIn('placement=$1\nmode=greedy',new)
        self.assertIn('--reads=786432 --writes=1474560',new)
        self.assertIn('RRZC_CAL_GREEDY_EXECUTE="$cal_greedy_execute"',new)
        self.assertIn('ZENFS_RRZC_CANDIDATE_POOL=original',new)
        self.assertIn('ZENFS_RR_STRICT=1 ZENFS_RR_ENABLE=1',new)
        self.assertIn('"$target/参考起点" matched',new)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'run.sh';path.write_text(new)
            subprocess.run(['bash','-n',str(path)],check=True)
        undo=new.replace('( $1 == G0 || $1 == G1 || $1 == G2 )','( $1 == greedy || $1 == blocks )',1)
        undo=undo.replace('placement=$1\nmode=greedy','mode=$1',1)
        undo=undo.replace('run_bench() {','pool_k=0\nif [[ $arm_tag == m1 ]]; then pool_k=1; fi\nflush_w=0\nrun_bench() {',1)
        undo=undo.replace('ZENFS_RRZC_PLACEMENT_MODE="$placement" ZENFS_RRZC_PLACEMENT_TRACE="${request_trace:+$result/组成.bin}"',
            'ZENFS_RRZC_POOL_FLUSH_W="$flush_w" ZENFS_RRZC_STREAM_CLASSIFY=1 ZENFS_RRZC_POOL_K="$pool_k" ZENFS_RRZC_POOL_TAGS="$target/受控标签.txt"',1)
        undo=undo.replace('hotspot=1;phase run','flush_w=1;hotspot=1;phase run',1)
        undo=undo.replace('hotspot=0;phase verify','flush_w=0;hotspot=0;phase verify',1)
        self.assertEqual(old,undo,'请求、维护或其他参数发生额外变化')

    def test_greedy_audit_checks_execution_not_model_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'zenfs.log'
            row='共同观测实际决策：轮次=1，执行Greedy=1，模型存在=1，模型Zone=200，Greedy存在=1，GreedyZone=100，实际存在=1，实际Zone=100\n'
            path.write_text(row);r=greedy_decisions(root,None)
            self.assertEqual(len(r),1);self.assertFalse(r[0]['改选'])
            path.write_text(row.replace('实际Zone=100','实际Zone=200'))
            with self.assertRaisesRegex(ValueError,'不是Greedy'):greedy_decisions(root,None)


if __name__=='__main__':unittest.main()
