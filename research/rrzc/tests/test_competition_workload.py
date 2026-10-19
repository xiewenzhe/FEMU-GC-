"""合成请求及候选测试，不作为真实竞争实验结果。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_competition_workload import planned_files, check_trace, expected_reads, coverage, MIB


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
        add(1,g,f"/rrzc-stream/g{g}.{('cold','warm','hot')[k]}.anchor.sst",'读4KiB',phase,offset,4096)
    return result


class CompetitionWorkloadTest(unittest.TestCase):
    def test_plan(self):
        self.assertEqual(sum(planned_files(0).values()),768)
        reads=expected_reads(180)
        counts={k:sum(g==0 and kind==k and phase=='更新后热读' for _,g,kind,phase,_ in reads) for k in range(3)}
        self.assertEqual(counts,{0:768,1:672,2:768})
        # 前三代完整；后三代被180秒边界截断，分别到年龄90、60、30秒。
        self.assertEqual([sum(r[1]==g for r in reads) for g in range(6)],
                         [13908,13908,13908,13548,11428,10908])
        self.assertEqual(len(reads),77608)

    def test_complete(self):
        self.assertEqual(check_trace(synthetic())['业务写入MiB'],4608)

    def test_missing(self):
        with self.assertRaises(ValueError):check_trace(synthetic()[:-1])

    def test_duplicate(self):
        rows=synthetic();rows[1]['偏移']='0'
        with self.assertRaises(ValueError):check_trace(rows)

    def test_wrong_file(self):
        rows=synthetic();rows[-1]['文件']='/rrzc-stream/错误'
        with self.assertRaises(ValueError):check_trace(rows)

    def test_competing_candidates(self):
        def line(marker,data):return marker+'，'.join(k+'='+str(v) for k,v in data.items())
        lines=[]
        for z,v,eta in ((1,16,30),(2,24,8),(3,32,4)):
            lines.append(line('简单双因素候选：',dict(Zone=z,有效字节=v*MIB,容量字节=256*MIB,覆盖资格=1,空目标资格=1)))
            lines.append(line('独立约束候选：',dict(Zone=z,短期预测可知=1,短期达阈值纳秒=eta*10**9)))
        lines.append(line('独立约束决策：',dict(轮次=1,GreedyZone=1,短期建议Zone=3,原会改选=1,
                     界限纳秒=10**10,预算字节=16*MIB,预测达阈值纳秒=4*10**9)))
        result=coverage(lines)
        self.assertEqual(result['成本优先改变建议数'],1)
        self.assertEqual(result['建议特征'][0]['预算内紧迫候选数'],2)
        self.assertEqual(len(result['建议特征'][0]['准入选项']),2)


if __name__=='__main__':unittest.main()
