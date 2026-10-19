"""成本日志的合成回归；端点减少不能作为未来寿命或收益证据。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_cost_observe import window_change, physical_bounds, fragment_summary, tail_flags, inspect, SECOND


class CostObserveAnalysis(unittest.TestCase):
    def point(self, second, valid, **changes):
        return dict({"会话":"1/2", "Zone":"1", "设备代际":"0", "主机代际":"0", "主机序号":"0",
                     "可知":"1", "时点纳秒":str(second*SECOND), "有效字节":str(valid)}, **changes)

    def test_decrease(self):
        old, now = self.point(30, 256), self.point(40, 32)
        self.assertEqual(window_change([{"1":old}], now, True, 10), (True, True, 30*SECOND, 256, 10*SECOND, 224, 0))

    def test_increase(self):
        old, now = self.point(10, 16), self.point(40, 32)
        self.assertEqual(window_change([{"1":old}], now, True, 30)[-2:], (0, 16))

    def test_same_value(self):
        old, now = self.point(30, 32), self.point(40, 32)
        self.assertEqual(window_change([{"1":old}], now, True, 10)[-2:], (0, 0))

    def test_unknown_current(self):
        self.assertFalse(window_change([{"1":self.point(30, 256)}], self.point(40, 32), False, 10)[0])

    def test_reset_session_and_unknown_rejected(self):
        for key, value in (("会话","3/4"), ("设备代际","1"), ("主机代际","1"), ("主机序号","2"), ("可知","0")):
            with self.subTest(key=key):
                old = self.point(30, 256, **{key:value})
                self.assertFalse(window_change([{"1":old}], self.point(40, 32), True, 10)[0])

    def test_recent_unknown_cannot_skip_back(self):
        history = [{"1":self.point(30, 256)}, {"1":self.point(39, 32, 可知="0")}]
        self.assertFalse(window_change(history, self.point(40, 32), True, 10)[0])

    def test_old_endpoint_keeps_unknown_evidence(self):
        result = window_change([{"1":self.point(27, 256)}], self.point(40, 32), True, 10)
        self.assertEqual(result, (False, True, 27*SECOND, 256, 13*SECOND, 0, 0))

    def test_actual_interval_not_fixed_window(self):
        result = window_change([{"1":self.point(29, 256)}], self.point(40, 32), True, 10)
        self.assertEqual(result[4], 11*SECOND)

    def test_newer_than_window_not_used(self):
        self.assertFalse(window_change([{"1":self.point(31, 256)}], self.point(40, 32), True, 10)[0])

    def test_no_history(self):
        self.assertFalse(window_change([], self.point(40, 32), True, 10)[0])

    def test_invalid_window(self):
        self.assertFalse(window_change([{"1":self.point(30, 256)}], self.point(40, 32), True, 9)[0])

    def test_alignment_and_sparse_unknown(self):
        self.assertEqual(physical_bounds(32<<20, 4096), (True, 32<<20, (32<<20)+4096))
        self.assertEqual(physical_bounds(4095, 4096), (True, 4096, 8192))

    def test_bad_physical_sizes(self):
        for length, alignment in ((0,4096), (1,0), (2**64-1,4096), (2**32-1,4096)):
            with self.subTest(length=length): self.assertFalse(physical_bounds(length,alignment)[0])

    def fragment(self, length, index=1):
        known, low, high = physical_bounds(length,4096)
        return {"次序":str(index), "逻辑字节":str(length), "物理界限可知":str(int(known)),
                "物理下界字节":str(low), "物理上界字节":str(high)}

    def test_fragments_sum(self):
        result = fragment_summary([self.fragment(4096), self.fragment(8192,2)],4096)
        self.assertEqual(result, (2,12288,True,12288,20480,12288,4096,8192))

    def test_wrong_fragment_bound_rejected(self):
        p = self.fragment(4096); p["物理下界字节"] = "1"
        with self.assertRaises(ValueError): fragment_summary([p],4096)

    def test_fragment_order_rejected(self):
        with self.assertRaises(ValueError): fragment_summary([self.fragment(4096,2)],4096)

    def test_tail_unknown_is_not_incompatible(self):
        physical = fragment_summary([self.fragment(4096)],4096)
        self.assertEqual(tail_flags({"可知":"0", "存在":"1", "剩余字节":"0"},physical), (False,False))

    def test_tail_absent_does_not_mean_no_other_target(self):
        physical = fragment_summary([self.fragment(4096)],4096)
        self.assertEqual(tail_flags({"可知":"1", "存在":"0", "剩余字节":"0"},physical), (False,False))

    def test_tail_bounds_have_ambiguous_middle(self):
        physical = fragment_summary([self.fragment(4096)],4096)
        target = {"可知":"1", "存在":"1", "剩余字节":"4096"}
        self.assertEqual(tail_flags(target,physical), (False,False))
        target["剩余字节"] = "4095"; self.assertEqual(tail_flags(target,physical), (True,False))
        target["剩余字节"] = "8192"; self.assertEqual(tail_flags(target,physical), (False,True))

    def sample_lines(self, stage, second):
        result = []
        for i in range(3,32):
            p = self.point(second,32, Zone=str(i*(256<<20)))
            p["阶段"] = stage
            result.append("成本有效量采样："+"，".join(k+"="+v for k,v in p.items()))
        return result

    def test_interleaved_business_log_does_not_split_sample_batch(self):
        waiting = self.sample_lines("等待",30)
        waiting.insert(10,"其他线程的正常业务日志")
        lines = waiting+["V1增长采样：可用=1"]+self.sample_lines("选区",40)+[
            "成本预留观察：轮次=1，启用=1，可知=1，存在=0，Zone=0，剩余字节=0",
            "简单双因素实际集合：实际选中=0"]
        candidates, samples = inspect(lines,{"1":{"帧可用":"1"}},4096)
        self.assertEqual((candidates,len(samples)),([],58))

    def test_incomplete_io_sample_batch_rejected(self):
        lines = self.sample_lines("等待",30)[:-1]+["V1增长采样：可用=1"]
        with self.assertRaises(ValueError): inspect(lines,{},4096)


if __name__ == "__main__": unittest.main()
