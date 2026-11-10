"""影子建议日志的合成单测，不生成实际性能或节省数字。"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import analyze_survivor_shadow as audit
import test_survivor_pair_audit as fixtures


class ShadowAuditTest(unittest.TestCase):
    def record(self,history,action):
        return (f'W旧策略影子：对象=10，历史={history}，需要=4096，可知=1，建议动作={action}，'
            '建议Zone=200，代际=0，修订=3，空闲Active=1，空闲Open=2，原因=合成单测，未申请令牌=1，未提交=1\n')

    def fixture(self,root,action):
        report=fixtures.PairAuditTest().fixture(root)
        text=(root/'run.zenfs.log').read_text()
        first,second,gate=text.splitlines(True)
        (root/'run.zenfs.log').write_text(first+self.record(0,1)+second+self.record(2,action)+gate)
        report['W历史准入']=audit.previous.audit_history(root,report,audit.MODE)
        return report

    def test_gate_redundancy_is_not_called_rerouting(self):
        original=audit.previous.analyze
        try:
            for action in [0,1,2]:
                with tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp);report=self.fixture(root,action)
                    audit.previous.analyze=lambda *_:report
                    result=audit.analyze(root,audit.MODE)['旧G2影子']
                    self.assertEqual(result['拒绝但原建议已回退数'],int(action==0))
                    self.assertEqual(result['拒绝覆盖原入池建议数'],int(action!=0))
        finally:audit.previous.analyze=original

    def test_unknown_and_bad_history(self):
        unknown=audit.parse_shadow('W旧策略影子未知：对象=10，原因=合成查询失败，原门控继续=1\n')
        self.assertEqual(unknown[0]['可知'],False)
        original=audit.previous.analyze
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);report=self.fixture(root,1)
                path=root/'run.zenfs.log'
                path.write_text(path.read_text().replace('历史=2','历史=0'))
                audit.previous.analyze=lambda *_:report
                with self.assertRaises(ValueError):audit.analyze(root,audit.MODE)
        finally:audit.previous.analyze=original


if __name__=='__main__':unittest.main()
