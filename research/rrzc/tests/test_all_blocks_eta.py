"""合成日志只检验ETA后续归因边界，不能冒充真实设备结果。"""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_all_blocks_eta import inspect

class EtaTest(unittest.TestCase):
    def prefix(self):return [
        '2026/10/25-00:00:00.000000 1 全块风险明细：轮次=1，Zone=128，块=1，代际=3，RC=1000，十秒增量=100，近期增量=20，近期间隔=2000000000，近期可知=1',
        '2026/10/25-00:00:00.000100 1 全块风险代表：轮次=1，Zone=128，代表块=1，存在=1，ETA=2400000000']
    def test_rr_before_reset(self):
        rows=inspect(self.prefix()+['2026/10/25-00:00:02.400100 1 RR风险状态：Zone=128，达阈值块=1'])
        self.assertEqual(rows[0]['当前ETA误差秒'],0)
    def test_reset_prevents_reused_zone_match(self):
        rows=inspect(self.prefix()+['2026/10/25-00:00:01.000000 1 Zone 重置诊断：Zone=128，状态=成功',
            '2026/10/25-00:00:02.400100 1 RR风险状态：Zone=128，达阈值块=1'])
        self.assertEqual(rows[0]['后续事件'],'Reset截止');self.assertIsNone(rows[0]['当前ETA误差秒'])
    def test_intervention_censored(self):
        row=inspect(self.prefix()+['2026/10/25-00:00:01.000000 1 时序方案决策：实际存在=1，实际Zone=128'])[0]
        self.assertIn('ZC介入',row['后续事件'])
    def test_verify_excluded(self):
        rows=inspect(self.prefix()+['全块业务计量截止：内容核验另计',
            '2026/10/25-00:00:02.400100 1 RR风险状态：Zone=128，达阈值块=1'])
        self.assertIn('截尾',rows[0]['后续事件'])

if __name__=='__main__':unittest.main()
