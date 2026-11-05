"""去边界独立入口的开关、同帧置零与兼容性；合成数据只用于单元测试。"""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from run_ycsb_noboundary_host import wrapper_source, analysis_source, SCHEDULE, RECORDS
from run_ycsb_quick_host import wrapper_source as old_wrapper
from analyze_deadline import choose, SCALE


class NoBoundaryTest(unittest.TestCase):
    def test_minimal_switch_and_old_entry_unchanged(self):
        old = old_wrapper()
        new = wrapper_source()
        self.assertIn('ZENFS_RRZC_TERMINAL="$enabled"', old)
        self.assertIn('ZENFS_RRZC_TERMINAL=0', new)
        self.assertEqual(new.replace('ZENFS_RRZC_TERMINAL=0', 'ZENFS_RRZC_TERMINAL="$enabled"')
            .replace('"$mode" "$enabled" 0 >', '"$mode" "$enabled" "$enabled" >'), old)
        for text in ('GC启动百分比=20', 'RR阈值=1024', 'ZENFS_RR_STRICT=1',
                     'ZENFS_RRZC_EXPECTED_RR="$enabled"', 'ZENFS_RRZC_JOINT_TIME=1'):
            self.assertIn(text, new)

    def test_shell_syntax(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'包装器.sh'
            path.write_text(wrapper_source())
            subprocess.run(['bash', '-n', str(path)], check=True)

    def test_analyzer_does_not_skip_audits(self):
        text = analysis_source()
        ast.parse(text)
        for marker in ('plans = space_audit(lines)', 'terminal=False', 'rr_changes(lines)',
                       "'strict.business.json'", 'check_trace(', 'digest(root/relative)', 'audit_forecasts('):
            self.assertIn(marker, text)
        self.assertIn("'边界配置：版本=2，启用=0，'", text)

    def test_zero_rr_keeps_identical_same_frame_choice(self):
        rows = [dict(z=z, v=v, cap=128, safe=1, p=0, credit=0, space=0)
                for z, v in ((1, 11), (2, 48), (3, 16))]
        self.assertEqual(choose(rows, (1, 128, 100))[1], 1)
        self.assertEqual(choose(rows, (1, 128, 100)), choose([dict(r) for r in rows], (1, 128, 100)))

    def test_actual_rr_can_change_choice_without_boundary(self):
        rows = [dict(z=1, v=10, cap=128, safe=1, p=0, credit=0, space=0),
                dict(z=2, v=12, cap=128, safe=1, p=SCALE, credit=12, space=116)]
        self.assertEqual(choose(rows, (1, 128, 100))[1], 2)
        self.assertEqual(choose([dict(r, p=0, credit=0, space=0) for r in rows], (1, 128, 100))[1], 1)

    def test_same_frame_counter_not_missing_key_false_positive(self):
        module = types.ModuleType('临时核验')
        exec(compile(analysis_source(), '去边界核验', 'exec'), module.__dict__)
        lines = ['时序方案需求：轮次=1，空闲=0，非空闲=500，分配等待=0，目标百分比=20',
                 '时序成本：轮次=1，Zone=1，有效=10，容量=128，安全=1',
                 '时序方案决策：轮次=1，实际存在=1，实际Zone=1']
        self.assertEqual(module.rr_changes(lines), 0)
        lines[-1] = '时序方案决策：轮次=1，实际存在=1，实际Zone=2'
        self.assertEqual(module.rr_changes(lines), 1)

    def test_preregistered_scale(self):
        self.assertEqual(RECORDS*4096, 4 << 30)
        self.assertEqual(SCHEDULE, (('f', 300000), ('b', 1200000)))


if __name__ == '__main__':
    unittest.main()
