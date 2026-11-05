"""五倍中测接线与分段边界测试；合成日志仅用于单元验证。"""
import ast
from pathlib import Path
import re
import subprocess
import sys
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from dbbench_hotspot_suite import wrapper_source,analysis_source
from dbbench_medium_stats import capture_source,observe_maintenance,observe_bench,operation_count,segment_rows
from run_dbbench_medium_host import READS,WRITES,PARENT,LABEL


class MediumTest(unittest.TestCase):
    def test_fivefold_counts_and_original_defaults(self):
        self.assertEqual((READS,WRITES),(2000000,3000000))
        text=wrapper_source(WRITES)
        subprocess.run(['bash','-n'],input=text,text=True,check=True)
        self.assertIn('writes=3000000',text)
        self.assertIn('|| writes=2048',text)
        self.assertIn('writes=600000',wrapper_source())
        self.assertIn('else 3000000',analysis_source(WRITES))
        self.assertIn('else 600000',analysis_source())
        self.assertIn('locality-v4',str(PARENT))
        self.assertNotIn('locality-v4',LABEL)
        with self.assertRaises(ValueError):wrapper_source(-1)

    def test_generated_capture_is_standalone_and_compiles(self):
        text=capture_source();ast.parse(text)
        module=types.ModuleType('独立中测采样器');exec(compile(text,'采样器','exec'),module.__dict__)
        self.assertTrue(callable(module.observe_maintenance))
        self.assertIn('observe_bench(state,data,name)',text)
        self.assertIn('分阶段维护=state.get',text)

    def test_incremental_maintenance_and_same_frame_decision(self):
        # 冻结函数在客机使用已有re全局；此处显式提供同一个模块。
        namespace={'re':re};exec(__import__('inspect').getsource(observe_maintenance),namespace)
        observe=namespace['observe_maintenance'];state={}
        observe(state,('统一成本：轮次=1，Zone=1，有效=10，安全=1\n'
            '统一成本：轮次=1，Zone=2，有效=20，安全=1\n'
            '时序方案决策：轮次=1，实际存在=1，实际Zone=2\n'
            'ZC 统计：轮次 1，本轮迁移 20 字节，本轮重置 1 个 Zone\n').encode(),'run')
        observe(state,('RR 统计：尝试 1 次，本次迁移 30 字节，本次重置 1 个 Zone\n'
            'Zone 重置诊断：Zone=2，状态=成功，主机代际=0/1\n').encode(),'run')
        box=state['分阶段维护']['run']
        self.assertEqual((box['RR次数'],box['ZC次数'],box['RR迁移字节'],box['ZC迁移字节']),(1,1,30,20))
        self.assertEqual(box['异于同帧Greedy数'],1)
        self.assertEqual(box['成功Reset'],1)
        observe(state,'ZC 统计：轮次 1，本轮迁移 99 字节，本轮重置 1 个 Zone\n'.encode(),'load')
        self.assertEqual(state['分阶段维护']['run']['ZC迁移字节'],20)

    def test_terminal_counts_fill_unreported_tail(self):
        namespace={'re':re};exec(__import__('inspect').getsource(observe_bench),namespace)
        state={};namespace['observe_bench'](state,(
            'Exiting the writer after 3000000 writes...\n'
            'readwhilewriting : 1.0 micros/op 1 ops/sec 1.0 seconds 2000000 operations;\n').encode(),'run')
        point=dict(进度={'run-线程0':2900000,'run-线程1':1900000},**state)
        self.assertEqual(operation_count(point),5000000)

    def test_milestones_keep_overshoot_and_do_not_interpolate(self):
        points=[]
        for n,total,amount in ((1,1100000,20),(2,3300000,50),(3,5000000,90)):
            points.append(dict(UTC=str(n),观测=dict(进度={'run-线程0':total},
                分阶段维护={'run':{'RR迁移字节':amount}})))
        rows=segment_rows(points,5000000)
        self.assertEqual([r['目标累计操作'] for r in rows],[1000000,2000000,4000000])
        self.assertEqual([r['观测累计操作下界'] for r in rows],[1100000,3300000,5000000])
        self.assertEqual(sum(r['本段迁移字节差'] for r in rows),90)


if __name__=='__main__':unittest.main()
