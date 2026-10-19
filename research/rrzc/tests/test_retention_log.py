"""留存评分及真实YCSB统计的合成回归，不代表设备收益。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from retention_log import RetentionLog
from analyze_short_shadow_stream import choose_short
from analyze_ycsb_retention import measurements

class RetentionTest(unittest.TestCase):
    def fixture(self,execute=True,retained=32):
        m=1<<20
        candidates=[dict(Zone=str(z),有效字节=str(v*m),容量字节=str(256*m),覆盖资格='1',空目标资格='1')
                    for z,v in ((1,16),(2,24),(3,32))]
        estimates={1:100*10**9,2:8*10**9,3:4*10**9}
        model=RetentionLog(execute)
        for z,v in ((1,16),(2,24),(3,32)):
            r=retained if z==3 else v
            model.observe(f'留存收益候选：轮次=1，Zone={z}，可知=1，有效字节={v*m}，十秒减少字节={(v-r)*m}，三十秒减少字节={(v-r)*3*m}，十秒实际间隔纳秒=10000000000，三十秒实际间隔纳秒=30000000000，预计保留字节={r*m}，额外迁移字节={(v-16)*m}，净收益字节={max(0,r-v+16)*m}')
        selected=2 if retained==32 else 3
        model.observe(f'留存收益建议：轮次=1，执行={int(execute)}，原建议Zone=3，新建议Zone={selected}，会改选=1，额外字节={(8 if selected==2 else 16)*m}，预测纳秒={(8 if selected==2 else 4)*10**9}')
        return model,candidates,estimates,choose_short(candidates,estimates)
    def test_flat_history_tie_uses_lower_valid(self):
        model,c,e,old=self.fixture()
        self.assertEqual(model.resolve('1',c,e,old)[1],2)
        model.finish(1)
    def test_observe_does_not_override(self):
        model,c,e,old=self.fixture(False)
        self.assertEqual(model.resolve('1',c,e,old),old)
    def test_tampered_forecast_rejected(self):
        model,c,e,old=self.fixture()
        model.candidates['1',2]['预计保留字节']='1'
        with self.assertRaises(ValueError):model.resolve('1',c,e,old)
    def test_unknown_cannot_keep_positive_score(self):
        model,c,e,old=self.fixture()
        model.candidates['1',2]['可知']='0'
        with self.assertRaises(ValueError):model.resolve('1',c,e,old)
    def test_all_unknown_falls_back_to_greedy(self):
        model,c,e,old=self.fixture()
        for r in model.candidates.values():r.update(可知='0',预计保留字节='0',净收益字节='0')
        model.suggestions['1'].update(新建议Zone='1',会改选='0',额外字节='0',预测纳秒='0')
        self.assertEqual(model.resolve('1',c,e,old)[1:5],(1,False,0,0))
    def test_ycsb_averages_and_counts(self):
        text='2026-10-17 01:00:00 1 sec: 7 operations; [READ: Count=3 Avg=2.10 95=3.00 99=3.10] [UPDATE: Count=4 Avg=8.00 95=9.00 99=9.20]'
        self.assertEqual(measurements(text,7)['READ']['平均微秒'],2.1)
        with self.assertRaises(ValueError):measurements(text,8)
        with self.assertRaises(ValueError):measurements(text.replace('UPDATE:','UPDATE-FAILED:'),7)

    def test_ycsb_p99_not_overwritten_by_p9999(self):
        text='1 sec: 7 operations; [READ: Count=3 Avg=211.81 95=230.27 99=840.19 99.9=12263.42 99.99=12894.21] [UPDATE: Count=4 Avg=238.70 95=238.46 99=851.46 99.9=12271.61 99.99=13385.73]'
        row=measurements(text,7)
        self.assertEqual(row['READ']['p99微秒'],840.19)
        self.assertEqual(row['UPDATE']['p99微秒'],851.46)
        with self.assertRaises(ValueError):measurements(text.replace('99=840.19','99.99=840.19'),7)
        with self.assertRaises(ValueError):measurements(text.replace('99=840.19','99=840.19 99=900.00'),7)

if __name__=='__main__':unittest.main()
