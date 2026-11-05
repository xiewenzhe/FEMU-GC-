"""合成日志验证同轮Greedy、模型总成本及闭环口径，不冒充实验收益。"""
from pathlib import Path
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_all_blocks_dbbench import selections,ZONE
from build_all_blocks_dbbench_guest import commands

class DbBenchTest(unittest.TestCase):
    def lines(self):
        a=25*ZONE;b=26*ZONE;lines=[]
        for z,v,p,credit in ((a,4,0,0),(b,5,10**9,5)):
            lines.extend([f'V1影子候选：轮次=1，Zone={z}，有效字节={v}',
                f'统一成本：轮次=1，Zone={z}，有效={v}，回收={128-v}，安全=1',
                f'时序成本：轮次=1，Zone={z}，有效={v}，容量=128，安全=1，概率十亿分数={p}，预计留存={v}，RR复制={credit}，RR空间=0'])
        lines.extend(['时序方案需求：轮次=1，缺口字节=38',
            f'时序方案候选：轮次=1，首区={a}，足够=1，计划区数=1，计划区={a}，预计ZC复制=4，静态回收=124，预计RR复制=5，预计RR空间=0，预计总复制=9',
            f'时序方案候选：轮次=1，首区={b}，足够=1，计划区数=1，计划区={b}，预计ZC复制=5，静态回收=123，预计RR复制=0，预计RR空间=0，预计总复制=5',
            f'时序方案决策：轮次=1，实际存在=1，实际Zone={b}',
            f'Zone 维护观测：阶段=ZC开始，Zone={b}',
            f'V1后续迁移复制：文件编号=23，文件名=/hot.sst，源Zone={b}，有效字节=5，复制成功=1',
            'V1后续映射提交：文件编号=23，成功=1',f'Zone 重置诊断：Zone={b}，状态=成功',
            f'独立约束维护结果：轮次=1，实际Zone={b}，本轮迁移字节=5，本轮重置区数=1，迁移成功=1',
            'ZC 统计：轮次 1，本轮迁移 5 字节'])
        return lines
    def test_switch_and_closed_loop(self):
        rounds,rows=selections(self.lines(),'业务','blocks')
        self.assertEqual(rounds[0]['安全可选Zone'],[25,26]);self.assertEqual(rounds[0]['GreedyZone'],25)
        self.assertEqual(rounds[0]['实际Zone'],26);self.assertTrue(rounds[0]['发生改选'])
        self.assertTrue(rounds[0]['ZC复制提交Reset闭环']);self.assertEqual(len(rows),2)
    def test_decision_without_reset_is_not_completed(self):
        lines=[s for s in self.lines() if 'Zone 重置诊断' not in s]
        rounds,_=selections(lines,'业务','blocks');self.assertFalse(rounds[0]['ZC复制提交Reset闭环'])
    def test_reset_before_commit_is_not_closed_loop(self):
        lines=self.lines();reset=next(s for s in lines if 'Zone 重置诊断' in s)
        lines.remove(reset);i=next(i for i,s in enumerate(lines) if 'V1后续映射提交' in s);lines.insert(i,reset)
        rounds,_=selections(lines,'业务','blocks');self.assertFalse(rounds[0]['ZC复制提交Reset闭环'])
    def test_false_plan_rejected(self):
        lines=[s.replace('预计总复制=5','预计总复制=4') for s in self.lines()]
        with self.assertRaisesRegex(ValueError,'成本重算'):selections(lines,'业务','blocks')
    def test_zero_demand_does_not_need_plans(self):
        lines=[s for s in self.lines() if '时序方案候选' not in s and '时序方案决策' not in s]
        lines=[s.replace('缺口字节=38','可知=1，缺口字节=0') for s in lines]
        lines.append('时序方案决策：轮次=1，实际存在=0，实际Zone=0')
        rounds,_=selections(lines,'业务','blocks');self.assertIsNone(rounds[0]['实际Zone'])
    def test_triggered_zero_demand_still_prices_one_victim(self):
        lines=[s.replace('缺口字节=38','可知=1，缺口字节=0') for s in self.lines()]
        lines.append('触发后选区约束：轮次=1，GC已触发=1，原始缺口字节=0，评估需求字节=1，零缺口仍选区=1')
        rounds,_=selections(lines,'业务','blocks')
        self.assertEqual(rounds[0]['实际Zone'],26)
        self.assertTrue(rounds[0]['ZC复制提交Reset闭环'])
        self.assertEqual(rounds[0]['空间需求']['缺口字节'],'0')
    def test_triggered_constraint_cannot_hide_a_positive_gap(self):
        lines=self.lines()+['触发后选区约束：轮次=1，GC已触发=1，原始缺口字节=38，评估需求字节=1，零缺口仍选区=1']
        with self.assertRaisesRegex(ValueError,'评估需求不符'):selections(lines,'业务','blocks')
    def test_greedy_not_minimum_rejected(self):
        with self.assertRaisesRegex(ValueError,'Greedy组'):selections(self.lines(),'业务','greedy')
    def test_not_selecting_is_not_switch(self):
        lines=[s.replace(f'实际存在=1，实际Zone={26*ZONE}','实际存在=0，实际Zone=0') for s in self.lines() if '时序方案' not in s]
        lines.append('统一决策：轮次=1，实际存在=0，实际Zone=0')
        rounds,_=selections(lines,'业务','blocks');self.assertFalse(rounds[0]['发生改选'])
    def test_native_commands(self):
        dry='echo CC;mkdir -p tools && g++ -c tools/db_bench_tool.cc -o tools/db_bench_tool.o\n'
        dry+='g++ tools/db_bench_tool.o tools/db_bench.o -lrocksdb_debug -lgflags -o db_bench\n'
        c,l=commands(dry);self.assertIn('-c',c);self.assertIn('-lgflags',l)
        with self.assertRaises(AssertionError):commands(dry+dry)
    def test_wrapper_syntax(self):
        subprocess.run(['bash','-n',str(Path(__file__).resolve().parent/'run_all_blocks_dbbench_guest.sh')],check=True)

if __name__=='__main__':unittest.main()
