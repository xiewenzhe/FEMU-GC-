"""半速固定计划及宿主失败识别的合成检查，不是设备结果。"""
import sys,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from run_rocksdb_anchor_slow_host import SlowRun,fatal_evidence
from analyze_rocksdb_observe import analyze
from analyze_rocksdb_anchor_slow import category,summarize

class SlowTest(unittest.TestCase):
    def candidate(self,round_id='1',rc=4000,known=True,urgent=True):
        return dict(轮次=round_id,Zone='805306368',合格=True,当前RC=rc,有效字节=100,
            双点证据=dict(增量可知=known,界限内=urgent,真实增量=0 if rc>=4096 else 100,
                         预测达阈值纳秒=0 if rc>=4096 else 1000000000))
    def test_overdue_is_not_threshold_forecast(self):
        self.assertEqual(category(self.candidate(rc=4462)),'当前已经达阈值')
        self.assertEqual(category(self.candidate()),'阈值前预测近期达阈值')
        self.assertEqual(category(self.candidate(known=False)),'未知')
        self.assertEqual(category(self.candidate(urgent=False)),'阈值前未预测紧迫')
    def test_repeated_suggestions_deduplicate_zone_epoch(self):
        candidates=[self.candidate('1',4462),self.candidate('2',4462)]
        paired=dict(候选观察=candidates,影子建议=[dict(轮次=c['轮次'],建议Zone=c['Zone'],会改选='1',额外有效字节='10') for c in candidates])
        blocks={(c['轮次'],c['Zone']):dict(设备会话='1/2',设备代际='3',块编号='4') for c in candidates}
        result=summarize(paired,blocks)
        self.assertEqual((result['影子改选建议次数'],result['建议去重区代际数']),(2,1))
        blocks['2','805306368']['设备代际']='4'
        self.assertEqual(summarize(paired,blocks)['建议去重区代际数'],2)
    def test_missing_suggested_candidate_rejected(self):
        with self.assertRaises(ValueError):summarize(dict(候选观察=[],影子建议=[dict(会改选='1',轮次='1',建议Zone='805306368')]),{})
    def test_plan_keeps_counts_and_production_settings(self):
        p=SlowRun.plan_updates
        self.assertEqual(p['覆盖写秒数']*p['覆盖写速率键每秒'],737280)
        self.assertEqual(SlowRun.profile,'anchors180slow')
        self.assertNotIn('额外预算MiB',p);self.assertNotIn('RR阈值',p);self.assertNotIn('GC启动比例',p)
    def test_slow_family_explicit_and_exclusive(self):
        root=Path('rocksdb-anchor-slow-20261016')
        with self.assertRaises(ValueError):analyze(root,{})
        with self.assertRaises(ValueError):analyze(root,{},anchor=True,anchor_slow=True)
        # 显式入口通过家族核验后仍要求完整退出码，不能接受失败前缀。
        with self.assertRaises(FileNotFoundError):analyze(root,{},anchor_slow=True)
    def test_normal_progress_not_failure(self):
        self.assertFalse(fatal_evidence('状态稳定\n1,1,2000,3000\n'))
        self.assertTrue(fatal_evidence('IO error: Zone allocation failure'))
        self.assertTrue(fatal_evidence('业务后台错误'))
    def test_host_stops_on_error_even_if_wrapper_finished(self):
        with TemporaryDirectory() as tmp:
            run=SlowRun(SimpleNamespace(label='合成',seconds=180,space_weight=50),None,Path(tmp))
            with patch.object(run,'ssh',return_value='已结束\n业务后台错误'),patch.object(run,'record'):
                with self.assertRaises(RuntimeError):run.observe_until('marker','true',30)
    def test_host_accepts_clean_finish_without_sleep(self):
        with TemporaryDirectory() as tmp:
            run=SlowRun(SimpleNamespace(label='合成',seconds=180,space_weight=50),None,Path(tmp))
            with patch.object(run,'ssh',return_value='已结束'),patch.object(run,'record'),patch('time.sleep') as sleep:
                run.observe_until('marker','true',30);sleep.assert_not_called()

if __name__=='__main__':unittest.main()
