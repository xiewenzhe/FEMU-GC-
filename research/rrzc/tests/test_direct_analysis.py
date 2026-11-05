"""统一模型公式、长尾解析与擦除代际边界；合成数据不代表性能。"""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from direct_analysis import estimate,extended_latency,erases,spare_fits
from analyze_ycsb_retention import audit


class DirectAnalysisTest(unittest.TestCase):
    def test_real_case_without_linear_discount(self):
        c=dict(有效字节='49776856',容量字节='268435456')
        b=dict(统计准入='1',当前RC='3919',窗口标志='7',十秒真实增量='213')
        f=dict(可知='1',十秒间隔='10036564865',三十秒间隔='30036596228',十秒减少='0',三十秒减少='13721308')
        r=estimate(c,b,f)
        self.assertLess(r['ETA'],10**10);self.assertGreater(r['RR抵扣'],39963278)
        self.assertEqual(r['RR抵扣'],r['预计留存'])
        f['可知']='0';self.assertEqual(estimate(c,b,f)['RR抵扣'],0)
        b['当前RC']='4096';self.assertEqual(estimate(c,b,f)['RR抵扣'],49776856)
        self.assertEqual(estimate(c,None,f)['净复制'],49776856)

    def test_all_requested_quantiles_are_distinct(self):
        text='1 sec: 10 operations; [READ: Count=6 Avg=2.3 99=8 99.9=9 99.99=10] [UPDATE: Count=4 Avg=3.4 99=11 99.9=12 99.99=13]'
        r=extended_latency(text,10)
        self.assertEqual((r['READ']['p99微秒'],r['READ']['p999微秒'],r['READ']['p9999微秒']),(8,9,10))
        for bad in (text.replace('99.9=9',''),text.replace('99.9=9','99.9=7'),text.replace('99.99=10','99.99=10 99.99=10')):
            with self.assertRaises(ValueError):extended_latency(bad,10)

    def test_all_zone_erases_not_rr_plus_zc(self):
        a=dict(会话高=1,会话低=2,块数每区=64,代际=[0]*32)
        b=copy.deepcopy(a);b['代际'][0]=3;b['代际'][7]=5
        r=erases(a,b);self.assertEqual((r['设备Zone擦除次数'],r['模拟NANDBlock擦除次数']),(8,512))
        for change in ('会话高','块数每区'):
            c=copy.deepcopy(b);c[change]+=1
            with self.assertRaises(ValueError):erases(a,c)
        with self.assertRaises(ValueError):erases(b,a)

    def test_direct_audit_is_opt_in(self):
        with self.assertRaises(ValueError):audit(Path('/没有实验数据'),'direct')
        with self.assertRaises(ValueError):audit(Path('/没有实验数据'),'greedy',direct_model=True)

    def test_scheduler_precedes_first_device_probe(self):
        p=Path(__file__).with_name('run_direct_ycsb_guest.sh').read_text()
        self.assertLess(p.index("printf 'mq-deadline"),p.index('"$build/reset_generation_probe"'))

    def test_spare_requires_true_empty_and_stable_identity(self):
        r=dict(Zone='2',容量='268435456',启用='1/1',可知='1/1',存在='1/1',
            替补Zone='8321499136/8321499136',剩余='268435456/268435456',有效='0/0',
            写指针='8321499136/8321499136',代际='3/3',序号='6/6')
        self.assertTrue(spare_fits(r,30342656,True))
        for key,value in (('可知','0/0'),('存在','0/0'),('启用','0/0'),('代际','3/4'),
            ('序号','6/8'),('有效','4096/4096'),('写指针','0/0'),('剩余','1/1')):
            bad=dict(r);bad[key]=value;self.assertFalse(spare_fits(bad,30342656,True))
        self.assertFalse(spare_fits(r,268435457,True))
        self.assertFalse(spare_fits(r,30342656,False))

    def test_real_failure_has_six_rounds_of_destination_only_rejection(self):
        from analyze_v1_shadow_stream import fields
        p=Path('/home/data/xwz/rrzc-baselines/direct-ycsb-r2-20261020-results/direct/现场维护.log')
        if not p.is_file():self.skipTest('没有原始失败日志，不用合成内容替代真实证据')
        lines=p.read_text().splitlines()
        decisions=[fields(s,'统一决策：') for s in lines if '统一决策：' in s]
        blocked=decisions[-6:]
        self.assertEqual([int(d['轮次']) for d in blocked],list(range(34,40)))
        self.assertTrue(all(d['实际存在']=='0' and d['排序数']=='13' for d in blocked))
        rows=[fields(s,'统一成本：') for s in lines if '统一成本：轮次=34，' in s]
        self.assertEqual(len(rows),13)
        self.assertTrue(all(r['源稳定']==r['覆盖']=='1' and r['安全']==r['空目标']==r['预留稳定']=='0' for r in rows))
        cold=min(rows,key=lambda r:int(r['有效']))
        self.assertEqual((cold['有效'],cold['物理上界'],cold['预留剩余']),('30341899','30342656','6884864'))
        acquisitions=[s for s in lines if '公共迁移空替补取得：' in s]
        self.assertIn('Zone=8321499136',acquisitions[-1])
        self.assertIn('容量字节=268435456',acquisitions[-1])
        self.assertNotIn('公共迁移替补接续：','\n'.join(lines[lines.index(acquisitions[-1])+1:]))


if __name__=='__main__':unittest.main()
