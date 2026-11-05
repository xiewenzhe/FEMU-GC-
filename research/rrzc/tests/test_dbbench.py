"""原生db_bench接线及解析测试；合成输入明确只作功能验证。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from dbbench_suite import wrapper_source, CASES, cpu_sets
from analyze_dbbench import summary, histograms, rr_changes, analyze
from capture_dbbench_guest import progress


class DbBenchTest(unittest.TestCase):
    def test_shell_and_fixed_rules(self):
        text=wrapper_source()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'包装器.sh';path.write_text(text)
            subprocess.run(['bash','-n',str(path)],check=True)
        for marker in ('ZENFS_RR_STRICT=1','ZENFS_RRZC_TERMINAL=0','RR阈值=1024',
                       'GC启动百分比=20','--finish_after_writes=1','--read_random_exp_range=10',
                       '--cache_size=0','--disable_wal=1','--verify_checksum=1'):
            self.assertIn(marker,text)
        self.assertNotIn('__DBBENCH_ENV__',text)
        self.assertNotIn('--readwrites=',text)
        self.assertNotIn('YCSB_RANDOM_SEED',text)

    def test_actual_native_output_parser(self):
        # 已归档真实普通目录小测，只证明解析，不冒充ZNS性能。
        path=Path('/home/data/xwz/rrzc-baselines/joint-dbbench8-parallel-v2-20261023-results/prepare/joint-dbbench8-parallel-v2-20261023/本地小测.log')
        if not path.exists():self.skipTest('真实小测日志不可用')
        text=path.read_text();start=text.index('readrandomwriterandom :')
        self.assertEqual(summary(text,'fillseq')['次数'],1024)
        self.assertEqual(summary(text,'readrandomwriterandom')['次数'],8192)
        hist=histograms(text[start:])
        self.assertEqual(hist['READ']['次数'],7380)
        self.assertEqual(hist['WRITE']['次数'],812)
        self.assertEqual(hist['READ']['P99.99微秒'],1457.20)

    def test_bad_or_missing_native_summary_rejected(self):
        with self.assertRaises(ValueError):summary('示例：没有真实输出','readrandom')
        with self.assertRaises(ValueError):histograms('缺少直方图')

    def test_same_frame_zero_rr_is_not_false_change(self):
        rows=['时序方案需求：轮次=1，空闲=0，非空闲=500，分配等待=0，目标百分比=20',
              '时序成本：轮次=1，Zone=1，有效=10，容量=128，安全=1',
              '时序方案决策：轮次=1，实际存在=1，实际Zone=1']
        self.assertEqual(rr_changes(rows),[])

    def test_progress_requires_operation_not_maintenance(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'phase.txt').write_text('run')
            (root/'run.log').write_text('示例 ... thread 0: (1000,2000) ops and 示例\n')
            with patch('capture_dbbench_guest.resource.setrlimit'):
                first=progress(root);second=progress(root)
            self.assertEqual(first['进度'],{'run-线程0':2000})
            self.assertEqual(first['进度'],second['进度'])
            self.assertEqual(second['RR完成'],0)

    def test_preregistered_loads(self):
        self.assertEqual(CASES,{'hot':1200000,'mixed95':1200000,'mixed50':300000})

    def test_hybrid_cpu_pairs_use_same_core_type(self):
        topology={c:('0',str(c//2)) if c<16 else ('0',str(c-8)) for c in range(28)}
        a,b=cpu_sets(list(range(28)),2,topology)
        self.assertEqual(len(a),8)
        self.assertEqual(len(b),8)
        self.assertFalse(set(a)&set(b))
        self.assertTrue(all(c<16 for c in a+b))

    def test_real_zns_rr_without_zc_has_no_required_score_rows(self):
        base=Path('/home/data/xwz/rrzc-baselines/joint-dbbench8-parallel-v2-20261023-results')
        name=base.name.removesuffix('-results')
        for mode in ('space','model'):
            tag='smoke-hot-'+mode
            root=base/tag/name/tag
            if not root.exists():self.skipTest('真实ZNS小测归档不可用')
            row=analyze(root,tag,'hot',mode,8192,8192)
            self.assertTrue(row['验收通过'])
            self.assertEqual(row['RR次数'],1)
            self.assertEqual(row['ZC次数'],0)
            self.assertEqual(row['严格RR业务账目']['历史最高RC'],1024)


if __name__=='__main__':unittest.main()
