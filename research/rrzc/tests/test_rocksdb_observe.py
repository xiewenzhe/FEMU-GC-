"""真实负载分析的合成边界检查；不冒充设备结果。"""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_rocksdb_observe import past_window,delete_label,features,factor_summary,SECOND

class RocksObserveTest(unittest.TestCase):
    def sample(self,t,**updates):
        r={k:str(v) for k,v in dict(Zone=805306368,会话高位=1,会话低位=2,设备代际后=0,
            主机代际后=0,主机序号后=0,块编号=3,统计稳定=1,查询开始纳秒=t*SECOND,
            查询结束纳秒=t*SECOND+1000,设备快照纳秒=t*SECOND+500,RC=t*10,
            剩余字节=0,有效字节=1000-t,阶段=1,批次=t,映射覆盖字节=1000-t,
            十秒增量=100,三十秒增量=300,窗口标志=7).items()}
        r.update({k:str(v) for k,v in updates.items()});return r
    def fixture(self):return [self.sample(t) for t in range(15)],self.sample(15)
    def test_same_block_real_delta(self):
        h,c=self.fixture();r=past_window(h,c,2,True)
        self.assertTrue(r['可知']);self.assertEqual(r['同块真实读取增量'],30)
    def test_block_switch_unknown_not_zone_max_delta(self):
        h,c=self.fixture();h[-1]['块编号']='4'
        self.assertFalse(past_window(h,c,2,True)['可知'])
    def test_reset_and_missing_samples_unknown(self):
        h,c=self.fixture();h[-1]['设备代际后']='1'
        self.assertFalse(past_window(h,c,10)['可知'])
        h,c=self.fixture();self.assertFalse(past_window(h[:3],c,10)['可知'])
    def test_valid_drop_uses_only_closed_same_epoch(self):
        h,c=self.fixture();r=past_window(h,c,10)
        self.assertTrue(r['可知']);self.assertEqual(r['净减少字节'],11)
        h[-1]['剩余字节']='10';self.assertFalse(past_window(h,c,10)['可知'])
    def test_valid_increase_unknown(self):
        h,c=self.fixture();c['有效字节']='2000'
        self.assertFalse(past_window(h,c,10)['可知'])
    def test_future_snapshot_cannot_enter_history(self):
        h,c=self.fixture();future=self.sample(16,RC=999999)
        self.assertEqual(past_window(h+[future],c,2,True),past_window(h,c,2,True))
    def test_deleted_label_is_after_cutoff_and_bounded(self):
        d={'x':[9*SECOND,15*SECOND]}
        self.assertTrue(delete_label('x',10*SECOND,25*SECOND,d)['十秒内成功删除'])
        self.assertFalse(delete_label('x',15*SECOND,30*SECOND,d)['十秒内成功删除'])
    def test_end_censor_is_not_negative(self):
        r=delete_label('x',10*SECOND,15*SECOND,{})
        self.assertFalse(r['观察完整']);self.assertIsNone(r['十秒内成功删除'])
    def test_missing_mapping_and_metadata_not_expiry(self):
        z=self.sample(1,映射覆盖字节=0)
        r=features([z,self.sample(20,阶段=2)],[],[],[])
        self.assertFalse(r['区快照与事前事后分栏'][0]['事前可见']['映射与有效量一致'])
        self.assertIsNone(r['区快照与事前事后分栏'][0]['事后标签']['十秒内成功删除的已映射SST字节'])
    def mapped(self,deleted):
        z=self.sample(1);mapping=[dict(批次='1',Zone=z['Zone'],文件='x.sst',区内有效字节='999')]
        live=[dict(批次='1',文件='x.sst',正在Compaction='1',层级='0')]
        events=[dict(事件='SST创建',成功='1',文件='x.sst',经过纳秒='0')]
        if deleted:events.append(dict(事件='SST删除',成功='1',文件='x.sst',经过纳秒=str(5*SECOND)))
        return features([z,self.sample(20,阶段=2)],mapping,live,events)
    def test_future_delete_does_not_change_pre_features(self):
        a,b=self.mapped(True),self.mapped(False)
        self.assertEqual(a['文件快照与事前事后分栏'][0]['事前'],b['文件快照与事前事后分栏'][0]['事前'])
        self.assertNotEqual(a['文件快照与事前事后分栏'][0]['事后标签'],b['文件快照与事前事后分栏'][0]['事后标签'])
    def test_summary_does_not_invent_trend_from_unknown(self):
        r=factor_summary(dict(可观察信号=self.mapped(True)))
        self.assertEqual(r['过去有效量变化与后续删除']['过去十秒下降']['区快照样本数'],0)
        self.assertEqual(r['当前Compaction状态与后续删除']['True']['十秒内成功删除样本数'],1)

if __name__=='__main__':unittest.main()
