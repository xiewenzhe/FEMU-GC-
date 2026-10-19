"""有效量趋势的合成边界验证；合成删除不冒充设备实验或真实机制收益。"""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_validity_signal import trend,migration_zones,future_deletions,groups,inspect,SECOND

class ValiditySignalTest(unittest.TestCase):
    def fixture(self,removals=None):
        removals=removals or {};zones=[];parts={}
        for t in range(41):
            p=[dict(文件编号=str(i),文件=f'{i}.sst',片段起点=str(4096*i),区内有效字节='1024',
                批次=str(t),阶段='1',Zone='805306368') for i in range(3) if t<removals.get(i,100)]
            valid=len(p)*1024
            z={k:str(v) for k,v in dict(批次=t,阶段=1,Zone=805306368,会话高位=1,会话低位=2,
                设备代际前=0,设备代际后=0,主机代际前=0,主机代际后=0,主机序号前=0,主机序号后=0,
                查询开始纳秒=t*SECOND,查询结束纳秒=t*SECOND+1000,设备快照纳秒=t*SECOND+500,
                统计稳定=1,窗口标志=1,块编号=0,RC=3800+5*t,
                有效字节=valid,映射覆盖字节=valid,容量字节=256<<20,剩余字节=0).items()}
            zones.append(z);parts[str(t),z['Zone']]=p
        return zones,parts
    def check_trend(self,removals=None,window=10):
        z,p=self.fixture(removals);return trend(z[:-1],z[-1],window,p)
    def test_zero_one_and_multiple_decreases(self):
        for removed,state in (({},'未下降'),({0:33},'单次下降'),({0:33,1:37},'多次下降')):
            r=self.check_trend(removed)
            self.assertTrue(r['可知']);self.assertEqual(r['分类'],state)
            self.assertEqual(r['净减少字节'],len(removed)*1024)
    def test_two_files_lost_in_one_interval_are_not_two_trend_steps(self):
        r=self.check_trend({0:33,1:33})
        self.assertEqual((r['分类'],r['下降区间数'],r['净减少字节']),('单次下降',1,2048))
    def test_posthoc_migration_audit_cannot_change_pre_features(self):
        z,p=self.fixture({0:33});mapping=[f for fragments in p.values() for f in fragments]
        a=inspect(z,mapping,[],[])['首个观察明细']
        b=inspect(z,mapping,[],['V1后续迁移复制：源Zone=805306368，目标Zone=100，复制成功=1'])['首个观察明细']
        self.assertEqual([r['事前趋势'] for r in a],[r['事前趋势'] for r in b])
        self.assertTrue(all(r['事后审计_整轮从未迁移触及'] for r in a))
        self.assertFalse(any(r['事后审计_整轮从未迁移触及'] for r in b))
    def test_thirty_second_window_is_explicit_not_lifetime_prediction(self):
        r=self.check_trend({0:20,1:37},30)
        self.assertEqual(r['分类'],'多次下降');self.assertNotIn('预计失效秒',r)
        self.assertGreaterEqual(r['实际区间秒'],30)
    def test_reset_and_epoch_change_unknown(self):
        for key in ('会话高位','设备代际后','主机代际后','主机序号后'):
            z,p=self.fixture();z[-2][key]='9'
            self.assertFalse(trend(z[:-1],z[-1],10,p)['可知'])
    def test_maximum_block_change_does_not_invalidate_zone_volume_history(self):
        z,p=self.fixture({0:33});z[-2]['块编号']='20'
        self.assertTrue(trend(z[:-1],z[-1],10,p)['可知'])
    def test_append_sampling_gap_and_unstable_unknown(self):
        for key,value in (('剩余字节','1'),('统计稳定','0')):
            z,p=self.fixture();z[-2][key]=value
            self.assertFalse(trend(z[:-1],z[-1],10,p)['可知'])
        z,p=self.fixture()
        h=[r for r in z[:-1] if r['批次'] not in ('34','35','36','37')]
        self.assertFalse(trend(h,z[-1],10,p)['可知'])
    def test_increase_or_missing_mapping_not_expiry(self):
        z,p=self.fixture({0:33});z[-1]['有效字节']='4000'
        self.assertFalse(trend(z[:-1],z[-1],10,p)['可知'])
        z,p=self.fixture();p['39',z[-1]['Zone']]=[]
        self.assertFalse(trend(z[:-1],z[-1],10,p)['可知'])
    def test_changed_physical_fragment_unknown(self):
        z,p=self.fixture();p['40',z[-1]['Zone']][0]['片段起点']='999999'
        self.assertFalse(trend(z[:-1],z[-1],10,p)['可知'])
    def test_non_sst_or_duplicate_mapping_unknown(self):
        z,p=self.fixture();p['40',z[-1]['Zone']][0]['文件']='CURRENT'
        self.assertFalse(trend(z[:-1],z[-1],10,p)['可知'])
        z,p=self.fixture();p['40',z[-1]['Zone']].append(p['40',z[-1]['Zone']][0])
        self.assertFalse(trend(z[:-1],z[-1],10,p)['可知'])
    def test_future_history_cannot_change_current_signal(self):
        z,p=self.fixture({0:33});expected=trend(z[:-1],z[-1],10,p)
        future=copy.deepcopy(z[-1]);future.update(查询开始纳秒=str(41*SECOND),查询结束纳秒=str(41*SECOND+1000),有效字节='99999')
        self.assertEqual(trend(z[:-1]+[future],z[-1],10,p),expected)
    def test_short_history_unknown_not_no_decrease(self):
        z,p=self.fixture()
        self.assertFalse(trend(z[-5:-1],z[-1],10,p)['可知'])
    def test_future_deletion_label_not_zone_expiry(self):
        z,p=self.fixture({0:33,1:37});current=z[-1]
        r=future_deletions(current,p,{'2.sst':[45*SECOND]},51*SECOND)
        self.assertEqual(r['十秒内成功删除SST字节'],1024)
        self.assertIn('不等于',r['边界'])
        censored=future_deletions(current,p,{},44*SECOND)
        self.assertFalse(censored['完整十秒删除观察']);self.assertIsNone(censored['十秒内成功删除SST字节'])
    def test_deletion_before_cutoff_not_future_label(self):
        z,p=self.fixture()
        r=future_deletions(z[-1],p,{'0.sst':[39*SECOND]},51*SECOND)
        self.assertEqual(r['十秒内成功删除SST字节'],0)
    def test_migration_audit_marks_both_source_and_target(self):
        self.assertEqual(migration_zones(['V1后续迁移复制：源Zone=100，目标Zone=200，复制成功=0']),{'100','200'})
    def test_first_cohort_not_selected_by_future_deletion(self):
        z,p=self.fixture({0:15})
        mapping=[f for fragments in p.values() for f in fragments]
        no_future=inspect(z,mapping,[],[])
        future=inspect(z,mapping,[dict(事件='SST删除',成功='1',文件='0.sst',经过纳秒=str(15*SECOND))],[])
        a=[r for r in no_future['首个观察明细'] if r['窗口秒']==10 and r['范围']=='所有满区SST'][0]
        b=[r for r in future['首个观察明细'] if r['窗口秒']==10 and r['范围']=='所有满区SST'][0]
        self.assertEqual(a['事前趋势'],b['事前趋势']);self.assertEqual(a['批次'],b['批次'])
        self.assertEqual(b['事前趋势']['分类'],'未下降')
        self.assertGreater(b['事后标签']['十秒内成功删除SST字节'],0)
    def test_groups_do_not_count_censored_as_negative(self):
        r=groups([dict(事前趋势=dict(分类='未下降'),事后标签=dict(完整十秒删除观察=False,十秒内成功删除SST字节=None))])
        self.assertEqual(r['未下降']['末尾截断数'],1)
        self.assertEqual(r['未下降']['随后十秒无SST删除数'],0)

if __name__=='__main__':unittest.main()
