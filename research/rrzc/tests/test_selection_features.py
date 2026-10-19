"""可观察因素的合成回归；示例读史不代表真实设备。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_selection_features import past_window,gate_features,SECOND


def sample(t,rc,**changes):
    r=dict(session_hi=1,session_lo=2,zone=3,generation=0,block_id=0,ch=0,lun=0,plane=0,
        block=3,born=0,time=int(t*SECOND),rc=rc,returned=int(t*SECOND))
    r.update(changes);return r


class SelectionFeatures(unittest.TestCase):
    def history(self):
        return [sample(i/2,3800+16*i) for i in range(11)]

    def test_fixed_past_interval(self):
        r=past_window(self.history(),sample(5.1,3963),int(5.08*SECOND),2)
        self.assertTrue(r["可知"])
        self.assertEqual(r["真实增量"],67)
        self.assertAlmostEqual(r["实际区间秒"],2.1)

    def test_future_cannot_change_past(self):
        history=self.history();point=sample(5.1,3963);cutoff=int(5.08*SECOND)
        a=past_window(history,point,cutoff,2)
        history.extend([sample(6,5000),sample(7,0,generation=1)])
        self.assertEqual(a,past_window(history,point,cutoff,2))

    def test_returned_after_cutoff_excluded(self):
        history=self.history();history[-1]["returned"]=6*SECOND;history[-1]["rc"]=9999
        r=past_window(history,sample(5.1,3963),int(5.08*SECOND),2)
        self.assertTrue(r["可知"])
        self.assertEqual(r["最后过去快照纳秒"],int(4.5*SECOND))

    def test_maximum_block_switch_back_unknown(self):
        history=self.history();history[-2]["block_id"]=1;history[-2]["plane"]=1
        self.assertFalse(past_window(history,sample(5.1,3963),int(5.08*SECOND),2)["可知"])

    def test_reset_unknown(self):
        history=self.history();history[-2]["generation"]=1
        self.assertFalse(past_window(history,sample(5.1,3963),int(5.08*SECOND),2)["可知"])

    def test_session_unknown(self):
        history=self.history();history[-2]["session_lo"]=9
        self.assertFalse(past_window(history,sample(5.1,3963),int(5.08*SECOND),2)["可知"])

    def test_counter_rollback_unknown(self):
        history=self.history();history[-2]["rc"]=3999
        self.assertFalse(past_window(history,sample(5.1,3963),int(5.08*SECOND),2)["可知"])

    def test_sampling_gap_unknown(self):
        self.assertFalse(past_window([sample(0,3800)],sample(3,3900),4*SECOND,2)["可知"])

    def test_missing_window_unknown(self):
        self.assertFalse(past_window([sample(4,3900)],sample(5,3901),6*SECOND,2)["可知"])

    def test_stop_interval_not_positive(self):
        history=self.history();history[-1]["rc"]=history[-2]["rc"]
        r=past_window(history,sample(5.1,3944),int(5.08*SECOND),2)
        self.assertTrue(r["可知"])
        self.assertFalse(r["每完整间隔均有读取"])

    def test_short_final_no_read_not_false_stop(self):
        history=self.history();point=sample(5.1,history[-1]["rc"])
        self.assertTrue(past_window(history,point,int(5.08*SECOND),2)["每完整间隔均有读取"])

    def test_invalid_window_rejected(self):
        with self.assertRaises(ValueError):past_window([],sample(5,3900),6*SECOND,3)

    def factors(self):
        window={"可知":True,"每完整间隔均有读取":True,"预测纳秒":7*SECOND}
        return dict(原约束通过=True,约两秒过去观察=window,约五秒过去观察=window,
            十秒ETA纳秒=11*SECOND,三十秒ETA纳秒=20*SECOND)

    def test_fixed_gates(self):
        r=gate_features(self.factors())
        self.assertTrue(r["加5秒持续性"])
        self.assertFalse(r["加10秒平均速率紧迫度"])

    def test_future_labels_cannot_change_gate(self):
        f=self.factors();a=gate_features(f)
        f.update(后续事件="即将退役",预设寿命秒=1,文件名="示例热文件")
        self.assertEqual(a,gate_features(f))

    def test_original_tail_denial_not_overridden(self):
        f=self.factors();f["原约束通过"]=False
        self.assertFalse(any(gate_features(f).values()))

    def test_unknown_window_not_zero_speed(self):
        f=self.factors();f["约五秒过去观察"]={"可知":False}
        self.assertFalse(gate_features(f)["加5秒平均速率紧迫度"])


if __name__=="__main__":unittest.main()
