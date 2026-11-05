"""用合成日志验证报告拒绝无证据的允许、错开关及错预算；不作实测数据。"""
from pathlib import Path
import tempfile
import unittest
from analyze_pair_admission import choices


def record(**updates):
    f=dict(序号=1,阶段='新文件',Zone=7,类别=2,有效=10,账目有效=10,H=10,W=0,HW=0,
        剩余=100,申请=1,新文件容量上界=1,共区=1,会话='1/2',设备代际=3,快照=40,
        查询完整=0,H源压力可知=0,H源每秒速率=0,风险可知=0,代表块=0,RC=0,阈值=1024,
        ETA=18446744073709551615,窗口=10000000000,伴随复制上界=100,预算=0,
        允许=0,原因='逐块近期风险不完整，不承诺配对收益',查询错误='',执行准入=1)
    f.update(updates)
    return '配对准入决定：'+'，'.join(k+'='+str(v) for k,v in f.items())+'\n'


class AnalysisTest(unittest.TestCase):
    def test_unknown_rejection_is_not_claimed_as_real_rr_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'log';p.write_text(record())
            rows,summary=choices(p,1)
            self.assertEqual(summary['实际拒绝数'],1)
            self.assertEqual(summary['风险可知共区数'],0)
            self.assertEqual(rows[0]['原因'],'逐块近期风险不完整，不承诺配对收益')
            p.write_text(record(执行准入=0));_,summary=choices(p,0)
            self.assertEqual(summary['拒绝建议数'],1);self.assertEqual(summary['实际拒绝数'],0)

    def test_unproven_allow_or_wrong_mode_and_budget_stops(self):
        for changes in (dict(允许=1),dict(预算=1),dict(执行准入=0),dict(序号=2)):
            with self.subTest(changes=changes),tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'log';p.write_text(record(**changes))
                with self.assertRaises(ValueError):choices(p,1)


if __name__=='__main__':unittest.main()
