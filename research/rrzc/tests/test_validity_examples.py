"""同区后续清空的合成边界检查；不是设备实验收益。"""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_validity_examples import follow_empty
import test_validity_signal as fixtures
from analyze_validity_signal import SECOND

class ValidityExampleTest(unittest.TestCase):
    def fixture(self):
        z,_=fixtures.ValiditySignalTest().fixture();current=z[30];future=z[31:35]
        future[-1]=dict(future[-1],有效字节='0',RC='0',设备代际前='1',设备代际后='1',主机代际前='1',主机代际后='1',主机序号前='2',主机序号后='2')
        return current,future
    def test_confirmed_empty_reset_has_interval_not_exact_event_time(self):
        current,future=self.fixture();r=follow_empty(current,future)
        self.assertTrue(r['确认空区']);self.assertTrue(r['同时确认Reset'])
        self.assertEqual(r['首次观测有效量为零上界秒'],4)
        self.assertLess(r['前次非空查询起点下界秒'],3)
    def test_new_generation_with_new_data_not_past_original_state(self):
        current,future=self.fixture();future[0].update(设备代际前='1',设备代际后='1',主机代际前='1',主机代际后='1')
        self.assertFalse(follow_empty(current,future)['确认空区'])
    def test_unstable_session_or_one_sided_generation_change_rejected(self):
        for changes in (dict(统计稳定='0'),dict(会话高位='9'),dict(设备代际前='1',设备代际后='1')):
            current,future=self.fixture();future[0].update(changes)
            self.assertFalse(follow_empty(current,future)['确认空区'])
    def test_empty_after_ten_seconds_not_counted_inside_window(self):
        current,future=self.fixture();future[-1].update(查询开始纳秒=str(42*SECOND),查询结束纳秒=str(42*SECOND+1000))
        self.assertFalse(follow_empty(current,future)['确认空区'])
    def test_missing_future_not_imputed_as_no_retirement(self):
        current,_=self.fixture();r=follow_empty(current,[])
        self.assertFalse(r['确认空区']);self.assertIn('不等于',r['原因'])

if __name__=='__main__':unittest.main()
