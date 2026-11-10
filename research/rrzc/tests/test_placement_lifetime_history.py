"""验证历史回放不泄漏未来结局；合成数据仅用于方法单测。"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_placement_lifetime_history import history_at


class LifetimeHistoryTest(unittest.TestCase):
    def test_same_timestamp_future_event_is_not_visible(self):
        samples={1:dict(born=0,born_event=1,retired=100,retired_event=11)}
        before=history_at(samples,100,5,110,99,event=10)
        after=history_at(samples,100,5,110,99,event=12)
        self.assertEqual(before['随访未完整数'],1)
        self.assertEqual(before['已知及时退役数'],0)
        self.assertEqual(after['已知及时退役数'],1)

    def test_future_labels_do_not_change_visible_history(self):
        left={1:dict(born=0, retired=200), 2:dict(born=60, retired=150),
              3:dict(born=90, retired=300), 4:dict(born=110, retired=130)}
        right={1:dict(born=0, retired=None), 2:dict(born=60, retired=500),
               3:dict(born=90, retired=None), 4:dict(born=120, retired=None)}
        self.assertEqual(history_at(left,100,5,30,99), history_at(right,100,5,30,99))

    def test_incomplete_followup_is_not_zero_lifetime(self):
        samples={1:dict(born=0, retired=20), 2:dict(born=0),
                 3:dict(born=90), 4:dict(born=0, retired=3)}
        q=history_at(samples,100,5,30,99)
        self.assertEqual((q['已知及时退役数'], q['已知仍存活数'], q['随访未完整数']), (1,1,1))
        self.assertEqual(q['历史退役比例下界'], 1/3)
        self.assertEqual(q['历史退役比例上界'], 2/3)
        self.assertEqual(q['历史判断'], '历史不足以区分')


if __name__ == '__main__':unittest.main()
