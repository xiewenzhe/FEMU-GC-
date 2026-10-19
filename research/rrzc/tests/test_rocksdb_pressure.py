"""原空间资格的合成边界验证；不把示例当设备数据。"""
import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_rocksdb_pressure import space_selected,gc_audit
from analyze_rocksdb_observe import analyze as success_analyze

class PressureTest(unittest.TestCase):
    def test_exact_twenty_has_no_nonempty_candidate(self):
        for valid in (1,16<<20,48<<20,255<<20):self.assertFalse(space_selected(valid,256<<20,20))
    def test_nineteen_allows_98_percent_but_not_97(self):
        self.assertTrue(space_selected(7<<20,256<<20,19))
        self.assertFalse(space_selected(8<<20,256<<20,19))
    def test_original_low_pressure_threshold(self):
        self.assertTrue(space_selected(48<<20,256<<20,13))
        self.assertFalse(space_selected(64<<20,256<<20,13))
    def test_empty_and_tiny_rounding_still_excluded(self):
        for valid in (0,1):self.assertFalse(space_selected(valid,256<<20,10))
    def test_unentered_check_is_not_cleaning(self):
        r=gc_audit(['GC 检查：空闲=25 字节，非空闲=75 字节，空闲比例=25%，启动水平=20%，进入选区=0'])
        self.assertEqual((r['检查数'],r['进入选区数']),(1,0))
    def test_wrong_trigger_and_incomplete_round_rejected(self):
        for line in ('GC 检查：空闲=20 字节，非空闲=80 字节，空闲比例=20%，启动水平=20%，进入选区=0',
                     'GC 检查：空闲=20 字节，非空闲=80 字节，空闲比例=20%，启动水平=20%，进入选区=1'):
            with self.assertRaises(ValueError):gc_audit([line])
    def frame(self,target=1):
        lines=['GC 检查：空闲=10 字节，非空闲=90 字节，空闲比例=10%，启动水平=20%，进入选区=1']
        for zone,valid,garbage in ((805306368,16<<20,94),(1073741824,24<<20,91)):
            lines.append(f'ZC 候选：Zone {zone}，无效比例 {garbage}%，有效数据 {valid} 字节，读压力未知，选中 1')
            lines.append(f'简单双因素候选：Zone={zone}，有效字节={valid}，容量字节=268435456，覆盖资格=1，空目标资格={target}')
        lines+=['独立约束决策：轮次=1，存在Greedy=1','GC 选区汇总：检查空闲比例=10%，无效比例门槛=70%']
        return lines
    def test_multiple_candidates_and_empty_target_are_distinct(self):
        r=gc_audit(self.frame());self.assertEqual(r['至少两个合格单区的轮数'],1)
        r=gc_audit(self.frame(0));self.assertEqual(r['至少一个合格单区的轮数'],0)
        self.assertEqual(len(r['进入选区详情'][0]['原空间通过']),2)
    def test_tampered_space_result_rejected(self):
        lines=self.frame();lines[1]=lines[1].replace('选中 1','选中 0')
        with self.assertRaises(ValueError):gc_audit(lines)
    def test_success_analyzer_still_rejects_failed_pressure_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)/'rocksdb-pressure-20261016';run.mkdir()
            (run/'mkfs.exit').write_text('0');(run/'run.exit').write_text('1')
            with self.assertRaises(ValueError):success_analyze(run,{},pressure=True)
    def test_default_analyzer_cannot_accept_pressure_label(self):
        with self.assertRaises(ValueError):success_analyze(Path('rocksdb-pressure-20261016'),{})

if __name__=='__main__':unittest.main()
