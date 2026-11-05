"""合成日志仅用于吞吐量解析与坏输入测试，不是设备实验结果。"""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ycsb_throughput import throughput
from analyze_ycsb_locality import gc_checks


class ThroughputTest(unittest.TestCase):
    text='Load throughput(ops/sec): 50000\nRun runtime(sec): 1200.4\nRun operations(ops): 1200000\nRun throughput(ops/sec): 999.67\n'

    def test_real_print_precision_and_rate_limit(self):
        r=throughput(self.text,1200000,{'limit.ops':'1000'})
        self.assertEqual(r['平均ops每秒'],999.67)
        self.assertEqual(r['运行时间秒'],1200.4)
        self.assertEqual(r['配置限速ops每秒'],1000)
        self.assertIn('Cleanup',r['口径'])

    def test_unlimited_and_schedule(self):
        self.assertEqual(throughput(self.text,1200000,{})['配置限速ops每秒'],0)
        self.assertEqual(throughput(self.text,1200000,{'limit.file':'rates.txt'})['限速计划'],'rates.txt')

    def test_new_unlimited_profile_preserves_pressure_workload(self):
        # 配置检查不是设备性能测试；历史模板继续保留1000次/秒。
        def properties(name):
            result={}
            for line in (Path(__file__).resolve().parent/name).read_text().splitlines():
                if not line.strip() or line.startswith('#'):continue
                key,value=line.split('=',1)
                self.assertNotIn(key,result)
                result[key]=value
            return result
        legacy=properties('workload-sst-probe.properties')
        current=properties('workload-stable-unlimited.properties')
        self.assertEqual(legacy['limit.ops'],'1000')
        self.assertEqual(current,dict(legacy,recordcount='491520',**{'limit.ops':'0'}))
        self.assertNotIn('limit.file',current)
        measured=throughput(self.text,1200000,current)
        self.assertEqual(measured['配置限速ops每秒'],0)
        self.assertEqual(measured['限速计划'],'')

    def test_gc_branch_not_candidate_rejection(self):
        text='GC 检查：空闲=23 字节，非空闲=77 字节，空闲比例=23%，启动水平=20%，进入选区=0'
        self.assertEqual(gc_checks(text)['进入选区次数'],0)
        with self.assertRaises(ValueError):gc_checks(text.replace('进入选区=0','进入选区=1'))
        with self.assertRaises(ValueError):gc_checks(text.replace('空闲比例=23','空闲比例=22'))
        with self.assertRaises(ValueError):gc_checks('')

    def test_missing_duplicate_invalid_inconsistent_rejected(self):
        for bad in (self.text.replace('999.67','800'),self.text.replace('1200.4','0'),
            self.text.replace('1200.4','nan'),self.text.replace('999.67','inf'),
            self.text.replace('999.67','-1'),self.text.replace('999.67','错'),
            self.text+'Run runtime(sec): 1200.4\n',self.text.replace('Run throughput','Wrong throughput'),
            self.text.replace('1200000','1200001'),self.text.replace('1200000','1200000.5')):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):throughput(bad,1200000,{})
        with self.assertRaises(ValueError):throughput(self.text,1200000,{'limit.ops':'-1'})


if __name__=='__main__':unittest.main()
