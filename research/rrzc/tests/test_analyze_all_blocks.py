"""分析器拒绝不完整和不一致输入；示例日志只用于单元测试。"""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_all_blocks import analyze_arm

class AnalysisTest(unittest.TestCase):
    def fixture(self,root):
        (root/'wrapper.exit').write_text('0\n')
        (root/'run.log').write_text('全块受控功能对照通过\n')
        (root/'zenfs.run.log').write_text('全块对照起点：后续请求=0\n'
            '时序成本：轮次=1，Zone=128，有效=4，安全=1\n'
            '时序成本：轮次=1，Zone=256，有效=5，安全=1\n'
            '时序方案决策：轮次=1，实际存在=1，实际Zone=256\n'
            'ZC 统计：轮次 1，本轮迁移 5 字节，本轮重置 1 个 Zone\n'
            'Zone 重置诊断：Zone=256，状态=成功\n'
            '全块业务计量截止：内容核验另计\n'
            'RR 统计：尝试 1 次，本次迁移 5 字节\n')
        (root/'输入与映射.csv').write_text('请求,逻辑偏移,相对纳秒,当前映射Zone\n')
        (root/'结束空间.csv').write_text('历史最高RC,结束空闲\n1024,123\n')
        (root/'源区Reset核验.csv').write_text('Zone,Empty,设备代际,RC总和,十秒增量总和,三十秒增量总和\n256,1,1,0,0,0\n')
    def test_verify_phase_separated(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.fixture(root);result=analyze_arm(root)
            self.assertEqual(result['RR次数'],0)
            self.assertEqual(result['ZC次数'],1)
            self.assertEqual(result['总迁移统计字节'],5)
            self.assertEqual(result['改选次数'],1)
    def test_missing_cutoff_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.fixture(root)
            p=root/'zenfs.run.log';p.write_text(p.read_text().replace('全块业务计量截止：','没有分账：'))
            with self.assertRaisesRegex(ValueError,'没有划界'):analyze_arm(root)
    def test_reset_stale_count_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.fixture(root)
            (root/'源区Reset核验.csv').write_text('Zone,Empty,设备代际,RC总和,十秒增量总和,三十秒增量总和\n256,1,1,1,0,0\n')
            with self.assertRaisesRegex(ValueError,'残留'):analyze_arm(root)
    def test_query_error_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.fixture(root);p=root/'zenfs.run.log'
            p.write_text('全块查询失败：Zone=128\n'+p.read_text())
            with self.assertRaisesRegex(ValueError,'查询出现错误'):analyze_arm(root)

if __name__=='__main__':unittest.main()
