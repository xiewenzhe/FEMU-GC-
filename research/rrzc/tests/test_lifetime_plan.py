"""合成固定请求检验删除时序；不将合成记录当设备实验。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lifetime_plan import expected_reads,early_events,name,planned_files,check_trace,final_files,MIB

def fixture(delay=0):
    rows=[];serial=[0,0,0]
    def add(t,g,n,op,phase,offset,size,at):
        rows.append(dict(线程=str(t),序号=str(serial[t]),成功='1',代号=str(g),文件=n,操作=op,
                         阶段=phase,偏移=str(offset),字节=str(size),开始纳秒=str(at),延迟纳秒='0'))
        serial[t]+=1
    for g in range(6):
        for n,v in planned_files(g).items():
            for i in range(v):add(0,g,n,'写1MiB','更新写',i*MIB,MIB,0)
    for due,g,k,phase,off in expected_reads():add(1,g,name(g,k),'读4KiB',phase,off,4096,due)
    for due,g,k in early_events(delay):add(2,g,name(g,k)+'.retired.bin','删除','旧版本失效',0,0,due*10**9)
    return rows

class LifetimePlanTest(unittest.TestCase):
    def test_fixed_plan(self):
        self.assertEqual(early_events(),[(68,0,0),(128,2,1),(128,2,2),(158,3,0)])
        self.assertEqual(len(expected_reads()),75260)
        business=sum(v for n,v in final_files().items() if '/filler' not in n)
        self.assertEqual(business,912*MIB)
    def test_complete_trace(self):
        self.assertEqual(check_trace(fixture()),dict(读请求数=75260,业务写入MiB=4608,预设早退役数=4))
    def test_delayed_delete_only(self):
        self.assertEqual(early_events(20),[(88,0,0),(148,2,1),(148,2,2),(178,3,0)])
        self.assertEqual(final_files(20),final_files())
        self.assertEqual(check_trace(fixture(20),20),check_trace(fixture()))
    def test_delay_cannot_use_original_timestamps(self):
        with self.assertRaises(ValueError):check_trace(fixture(),20)
    def test_unregistered_delay_rejected(self):
        with self.assertRaises(ValueError):early_events(5)
    def test_delete_too_early_rejected(self):
        r=fixture();r[-1]['开始纳秒']='0'
        with self.assertRaises(ValueError):check_trace(r)
    def test_missing_delete_rejected(self):
        with self.assertRaises(ValueError):check_trace(fixture()[:-1])
    def test_missing_read_rejected(self):
        r=fixture();r.pop(5000)
        with self.assertRaises(ValueError):check_trace(r)

if __name__=='__main__':unittest.main()
