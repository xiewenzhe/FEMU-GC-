"""后续分析的工作量一致、分账和文件身份检查；示例不属于实验结果。"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_all_blocks_followup import amounts,copies,arm

class FollowupAnalysisTest(unittest.TestCase):
    def test_amounts(self):
        r=amounts('ZC 统计：轮次 1，本轮迁移 4 字节\nRR 统计：尝试 1 次，本次迁移 5 字节\n')
        self.assertEqual(r['总迁移字节'],9);self.assertEqual(r['RR次数'],1)
    def test_copy_needs_commit(self):
        line='V1后续迁移复制：文件编号=21，文件名=/cold.sst，源Zone=128，目标Zone=256，有效字节=4，复制成功=1\n'
        with self.assertRaisesRegex(ValueError,'映射提交'):copies(line)
        r=copies(line+'V1后续映射提交：文件编号=21，成功=1\n')
        self.assertEqual(r[0]['文件名'],'/cold.sst')
    def fixture(self,root,branch='keep'):
        phases=['初始窗口结束','存活条件执行后',*[f'追加写入{i}' for i in range(1,5)],'追加观察结束']
        spans=[0,0,32<<20,64<<20,96<<20,128<<20,128<<20]
        text='阶段,追加逻辑字节,冷文件存在,空闲字节,有效字节,可回收字节,最高RC\n'
        for i,(stage,span) in enumerate(zip(phases,spans)):
            text+=f'{stage},{span},{int(i==0 or branch=="keep")},123,456,0,1024\n'
        (root/'阶段空间.csv').write_text(text)
        raw='追加阶段边界：阶段=初始窗口结束\n'
        for i in range(4):raw+=f'追加业务写入：序号={i}，文件=/append{i}.bin，逻辑字节={32<<20}\n'
        raw+='全块业务计量截止：内容校验另计\n';(root/'zenfs.run.log').write_text(raw)
        return dict(总迁移统计字节=0,结束空间=dict(结束空闲='123',结束有效='456',结束可回收='0'))
    def test_valid_branches(self):
        for branch in ('keep','retire'):
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);base=self.fixture(root,branch)
                with patch('analyze_all_blocks_followup.analyze_arm',return_value=base):
                    self.assertEqual(arm(root,branch)['追加窗口']['总迁移字节'],0)
    def test_wrong_append_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);base=self.fixture(root)
            p=root/'阶段空间.csv';p.write_text(p.read_text().replace(str(128<<20),str(127<<20)))
            with patch('analyze_all_blocks_followup.analyze_arm',return_value=base):
                with self.assertRaisesRegex(ValueError,'追加工作量'):arm(root,'keep')
    def test_retire_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);base=self.fixture(root,'keep')
            with patch('analyze_all_blocks_followup.analyze_arm',return_value=base):
                with self.assertRaisesRegex(ValueError,'存活条件'):arm(root,'retire')

if __name__=='__main__':unittest.main()
