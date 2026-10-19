"""使用明确合成请求测试核验器；不作为真实实验结果。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_model_workload import planned_files, check_trace, expected_reads, MIB


def synthetic():
    result=[];serial=[0,0]
    def add(t,g,name,op,phase,offset,size):
        result.append(dict(线程=str(t),序号=str(serial[t]),成功='1',代号=str(g),文件=name,
                           操作=op,阶段=phase,偏移=str(offset),字节=str(size)))
        serial[t]+=1
    for g in range(6):
        for name,size in planned_files(g).items():
            for i in range(size):add(0,g,name,'写1MiB','更新写',i*MIB,MIB)
    for _,g,k,phase,offset in expected_reads(180):
        add(1,g,f"/rrzc-stream/g{g}.{'hot' if k else 'cold'}.anchor.sst",'读4KiB',phase,offset,4096)
    return result


class ModelWorkloadTest(unittest.TestCase):
    def test_plan(self):
        for g,size in enumerate((16,24,32,16,24,32)):
            files=planned_files(g)
            self.assertEqual(sum(files.values()),512)
            self.assertEqual(files[f'/rrzc-stream/g{g}.hot.anchor.sst'],size)

    def test_complete(self):
        self.assertEqual(check_trace(synthetic())['业务写入MiB'],3072)

    def test_missing(self):
        with self.assertRaises(ValueError):check_trace(synthetic()[:-1])

    def test_duplicate_offset(self):
        rows=synthetic();rows[1]['偏移']='0'
        with self.assertRaises(ValueError):check_trace(rows)

    def test_wrong_read_file(self):
        rows=synthetic();rows[-1]['文件']='/rrzc-stream/错误文件'
        with self.assertRaises(ValueError):check_trace(rows)


if __name__=='__main__':unittest.main()
