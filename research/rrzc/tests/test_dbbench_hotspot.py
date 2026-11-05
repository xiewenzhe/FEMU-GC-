"""相邻读写热点接线测试；合成候选及CSV只作单元验证，不冒充实测。"""
import ast
import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from dbbench_hotspot_source import patch,patched
from dbbench_hotspot_suite import wrapper_source,analysis_source,layout


class HotspotTest(unittest.TestCase):
    def test_exact_patch_on_real_archived_native_source(self):
        source=Path('/home/data/xwz/rrzc-baselines/joint-dbbench8-parallel-v2-20261023-results/源码/db_bench_tool.cc')
        if not source.exists():self.skipTest('真实原生源不存在')
        text=patched(source.read_text())
        self.assertEqual(text.count('rrzc_dbbench_hotspot::Read('),1)
        self.assertEqual(text.count('rrzc_dbbench_hotspot::Write('),1)
        self.assertIn('#define NDEBUG 1',text)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'tools').mkdir()
            target=root/'tools/db_bench_tool.cc';target.write_bytes(source.read_bytes())
            subprocess.run(['git','apply','-'],cwd=root,input=patch(source.read_text()),text=True,check=True)
            self.assertEqual(target.read_text(),text)
            subprocess.run(['git','apply','--reverse','-'],cwd=root,input=patch(source.read_text()),text=True,check=True)
            self.assertEqual(target.read_bytes(),source.read_bytes())

    def test_greedy_flags_disabled_not_just_renamed(self):
        text=wrapper_source();subprocess.run(['bash','-n'],input=text,text=True,check=True)
        self.assertIn('planning=()',text)
        self.assertIn('"${planning[@]}"',text)
        self.assertIn('if [[ $mode == model ]]',text)
        self.assertIn('RRZC_DB_WRITE_HOT_PERCENT=80',text)
        self.assertIn('hotspot=0',text)
        self.assertIn('writes=600000',text)
        self.assertIn('RR阈值=1024',text)
        self.assertIn('GC启动百分比=20',text)

    def test_analyzer_respects_different_actual_paths(self):
        text=analysis_source();ast.parse(text)
        self.assertIn('else model_decisions(lines)',text)
        self.assertIn('space_plan=enabled, deadline=enabled',text)
        self.assertIn('if not enabled: audit_greedy(lines)',text)

    def test_greedy_minimum_valid_actual_decision(self):
        module=types.ModuleType('热点核验');exec(analysis_source(),module.__dict__)
        rows=['统一成本：轮次=1，Zone=1，有效=10，回收=134217718，净复制=10，RR抵扣=0，安全=1',
              '统一成本：轮次=1，Zone=2，有效=20，回收=134217708，净复制=20，RR抵扣=0，安全=1',
              '统一决策：轮次=1，实际存在=1，实际Zone=1']
        module.audit_greedy(rows)
        rows[-1]='统一决策：轮次=1，实际存在=1，实际Zone=2'
        with self.assertRaises(ValueError):module.audit_greedy(rows)

    def test_extent_join_reports_range_evidence_not_exact_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for index in (0,1):
                with (root/f'布局-{index}.sst.csv').open('w') as f:
                    w=csv.writer(f);w.writerow(['文件','层级','字节','最小键十六进制','最大键十六进制'])
                    w.writerow(['/1.sst',1,100,(0).to_bytes(8,'big').hex(),(4095).to_bytes(8,'big').hex()])
                    w.writerow(['/2.sst',1,100,(4096).to_bytes(8,'big').hex(),(65535).to_bytes(8,'big').hex()])
                with (root/f'布局-{index}.extent.csv').open('w') as f:
                    w=csv.writer(f);w.writerow(['文件','Zone','起点','字节'])
                    w.writerow(['/rrzc-dbbench/1.sst',128,128,100]);w.writerow(['/rrzc-dbbench/2.sst',128,228,100])
            data=layout(root,4096)
            self.assertEqual(data[0]['同区范围覆盖'],[128])
            self.assertIn('不能证明每次读取位置',data[0]['边界'])


if __name__=='__main__':unittest.main()
