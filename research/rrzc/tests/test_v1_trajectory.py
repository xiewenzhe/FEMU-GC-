"""合成样本只验证身份、Reset和区间判定，不是实际设备轨迹。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_v1_trajectory import cross, short_estimate, SECOND


def row(time,rc,**extra):
    value=dict(session_hi=1,session_lo=2,zone=3,generation=0,block_id=0,ch=0,lun=0,
               plane=0,block=3,born=0,time=int(time*SECOND),rc=rc)
    value.update(extra)
    return value


class Trajectory(unittest.TestCase):
    def test_cross_interval(self):
        result=cross([row(1,3990),row(1.5,4010),row(2,4100)],row(1,3990))
        self.assertEqual((result["下界秒"],result["上界秒"]),(.5,1))
        self.assertEqual(result["下界RC"],4010)

    def test_block_change(self):
        self.assertEqual(cross([row(2,4100,block_id=1,plane=1)],row(1,3990))["原因"],"最大块身份变化")

    def test_reset(self):
        self.assertEqual(cross([row(2,0,generation=1,born=2*SECOND)],row(1,3990))["原因"],"Reset代际变化")

    def test_session(self):
        self.assertEqual(cross([row(2,4100,session_lo=3)],row(1,3990))["原因"],"设备会话变化")

    def test_rollback(self):
        self.assertEqual(cross([row(2,10)],row(1,3990))["原因"],"同块同代际RC倒退")

    def test_end_unknown(self):
        self.assertEqual(cross([row(2,4000)],row(1,3990))["状态"],"未知")

    def test_already_reached(self):
        self.assertEqual(cross([],row(1,4096))["状态"],"决策时已达到阈值")

    def test_short_past_only(self):
        samples=[row(0,3900),row(.5,3910),row(1,3920),row(1.5,3930),row(2,3940),row(3,10000)]
        result=short_estimate(samples,row(2.2,3944))
        self.assertEqual(result["同块真实增量"],24)
        self.assertAlmostEqual(result["实际区间秒"],1.2)
        self.assertEqual(result["预测纳秒"],7600000000)

    def test_short_block_switch_back_unknown(self):
        result=short_estimate([row(0,3900),row(.5,3950,block_id=1,plane=1),row(1,3920)],row(1.2,3924))
        self.assertEqual(result["状态"],"未知")

    def test_short_missing_and_gap(self):
        self.assertEqual(short_estimate([],row(1,3990))["状态"],"未知")
        self.assertEqual(short_estimate([row(0,3990)],row(3,4000))["状态"],"未知")


if __name__=="__main__":
    unittest.main()
