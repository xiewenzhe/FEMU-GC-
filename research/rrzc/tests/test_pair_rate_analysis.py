"""双点速率核验的合成边界检查；不是设备结果。"""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_pair_rate import rate,pair_audit
from analyze_rocksdb_observe import analyze

class PairRateTest(unittest.TestCase):
    def fixture(self):
        zone=str(3*(256<<20))
        block=dict(Zone=zone,统计准入='1',设备会话='1/0',设备代际='0',块编号='0',PPA='0/0/0/3',
            代际起点纳秒='0',快照纳秒='11250000000',当前RC='3125',窗口标志='1')
        common=dict(Zone=zone,轮次='1',会话='1/0',阈值='4096',块数='64',代际='0',
            块编号='0',PPA='0/0/0/3',区编号='3',起点纳秒='0',标志='1')
        a=dict(common,位置='0',快照纳秒='10000000000',RC='3000')
        b=dict(common,位置='1',快照纳秒='11000000000',RC='3100')
        return block,{'0':a,'1':b}
    def test_real_delta_not_current_minus_latest(self):
        b,e=self.fixture();r=rate(b,e,2)
        self.assertEqual((r['增量可知'],r['真实增量'],r['预测达阈值纳秒']),(1,100,9710000000))
    def test_phase_near_current_is_allowed_but_oldest_age_is_bounded(self):
        b,e=self.fixture()
        for now in (11000000001,11250000000,12000000000):
            b['快照纳秒']=str(now);self.assertEqual(rate(b,e,2)['增量可知'],1)
        b['快照纳秒']='12000000001';self.assertEqual(rate(b,e,2)['增量可知'],0)
    def test_every_identity_field_and_reset_rejected(self):
        b,e=self.fixture()
        for k in ('会话','阈值','块数','代际','块编号','PPA','区编号','起点纳秒'):
            changed=copy.deepcopy(e);changed['0'][k]+='1'
            self.assertEqual(rate(b,changed,2)['增量可知'],0)
        b['块编号']='1';self.assertEqual(rate(b,e,2)['增量可知'],0)
    def test_time_rc_regression_and_too_close_rejected(self):
        b,e=self.fixture()
        for k,v in (('RC','3200'),('快照纳秒','11000000000'),('快照纳秒','10600000000')):
            f=copy.deepcopy(e);f['0'][k]=v;self.assertEqual(rate(b,f,2)['增量可知'],0)
        e['1']['RC']='3200';self.assertEqual(rate(b,e,2)['增量可知'],0)
    def test_zero_rate_known_not_infinite_urgency(self):
        b,e=self.fixture();e['1']['RC']='3000';r=rate(b,e,2)
        self.assertEqual((r['增量可知'],r['真实增量'],r['界限内']),(1,0,0))
        b['当前RC']='4096';self.assertEqual(rate(b,e,2)['预测达阈值纳秒'],0)
    def test_missing_endpoint_and_current_not_synthesized(self):
        b,e=self.fixture()
        self.assertEqual(rate(b,{'1':e['1']},2)['原因'],'端点区缺失')
        self.assertEqual(rate(b,e,1)['增量可知'],0)
        b['统计准入']='0';self.assertEqual(rate(b,e,2)['原因'],'当前统计或参数无效')
    def test_legacy_profile_cannot_accept_new_label(self):
        with self.assertRaises(ValueError):analyze(Path('rocksdb-pair-rate-20261016'),{})
        with self.assertRaises(ValueError):analyze(Path('rocksdb-pair-rate-20261016'),{},recent=True,paired=True)
    def test_unclosed_candidate_rejected(self):
        with self.assertRaises(ValueError):pair_audit(['双点速率候选：轮次=1，Zone=805306368'])
    def full_log(self):
        b,e=self.fixture();z=b['Zone'];b.update(轮次='1',原候选资格='1')
        def line(marker,row):return marker+'，'.join(f'{k}={v}' for k,v in row.items())
        c=dict(Zone=z,有效字节=str(16<<20),容量字节=str(256<<20),覆盖资格='1',空目标资格='1')
        r=dict(轮次='1',Zone=z,发布序号='2',端点数='2',原候选资格='1',统计准入='1',**rate(b,e,2))
        d=dict(轮次='1',存在Greedy='1',GreedyZone=z,建议Zone=z,会改选='0',额外有效字节='0',
               预测达阈值纳秒='9710000000',实际改选='0',原因='Greedy同块近期已经紧迫')
        return [line('简单双因素候选：',c),line('V1影子候选：',b),
                *[line('双点速率端点：',p) for p in e.values()],line('双点速率候选：',r),
                line('双点速率建议：',d),line('V1增长候选：',dict(轮次='1',Zone=z,增量可知='0')),
                '独立约束决策：轮次=1']
    def test_new_and_old_evidence_are_separate(self):
        r=pair_audit(self.full_log())
        self.assertEqual((r['旧增量可知数'],r['双点增量可知数'],r['实际改选数']),(0,1,0))
    def test_tampered_endpoint_or_executed_suggestion_rejected(self):
        for old,new in (('RC=3100','RC=3101'),('实际改选=0','实际改选=1')):
            with self.assertRaises(ValueError):pair_audit([s.replace(old,new) for s in self.full_log()])

if __name__=='__main__':unittest.main()
