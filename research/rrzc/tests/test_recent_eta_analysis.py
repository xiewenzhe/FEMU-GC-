"""合成分析器边界，不作为设备读速率或性能数据。"""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_recent_eta import validate_rate, fields


def sample():
    return dict(轮次='1',Zone='0',RC='976',阈值='1024',十秒增量='976',原ETA='491803279',新ETA='3000000000',
        近期可知='1',会话='1/2',前会话='1/2',代际='0',前代际='0',块='0',前块='0',PPA='0/0/0/2',前PPA='0/0/0/2',
        起点='0',前起点='0',近期增量='16',间隔='1000000000',快照='100000000000',前快照='99000000000',前RC='960',
        原抵扣='1',新抵扣='1')


def block(r, flags='7'):
    pairs=(('轮次','轮次'),('Zone','Zone'),('设备会话','会话'),('设备代际','代际'),
           ('块编号','块'),('PPA','PPA'),('快照纳秒','快照'),('代际起点纳秒','起点'),
           ('当前RC','RC'),('十秒真实增量','十秒增量'))
    return dict({a:r[b] for a,b in pairs},窗口标志=flags)


class RecentEtaAnalysisTest(unittest.TestCase):
    def test_known_rate(self):
        s=sample();r=validate_rate(s,block(s));self.assertEqual(r['新ETA秒'],3);self.assertEqual(r['近期读每秒'],16)

    def test_identity_cannot_be_replaced_by_zone_maximum_delta(self):
        for key in ('前会话','前代际','前块','前PPA','前起点'):
            r=sample();r[key]='不同'
            with self.assertRaises(ValueError):validate_rate(r,block(r))

    def test_wrong_delta_or_eta_rejected(self):
        for key in ('近期增量','间隔','新ETA','原ETA'):
            r=sample();r[key]='17'
            with self.assertRaises(ValueError):validate_rate(r,block(r))

    def test_missing_window_evidence_not_assumed_complete(self):
        with self.assertRaises(ValueError):validate_rate(sample())

    def test_reset_under_ten_seconds_retains_unknown_old_eta(self):
        s=sample();s.update(RC='18',十秒增量='18',前RC='18',近期增量='0',
            起点='95000000000',前起点='95000000000',原ETA=str(2**64-1),新ETA=str(2**64-1),原抵扣='0',新抵扣='0')
        r=validate_rate(s,block(s,'1'))
        self.assertFalse(r['十秒窗口完整']);self.assertIsNone(r['原ETA秒'])
        s['原ETA']='558888888889'
        with self.assertRaises(ValueError):validate_rate(s,block(s,'1'))

    def test_short_same_block_growth_does_not_require_full_ten_seconds(self):
        s=sample();s.update(起点='95000000000',前起点='95000000000',原ETA=str(2**64-1),原抵扣='0')
        r=validate_rate(s,block(s,'1'))
        self.assertIsNone(r['原ETA秒']);self.assertEqual(r['新ETA秒'],3)

    def test_flag_age_and_cross_snapshot_mismatch_rejected(self):
        s=sample();b=block(s);b['PPA']='不同物理块'
        with self.assertRaises(ValueError):validate_rate(s,b)
        s.update(起点='95000000000',前起点='95000000000')
        with self.assertRaises(ValueError):validate_rate(s,block(s))

    def test_disabled_or_lost_statistics_cannot_receive_credit(self):
        for flags in ('0','9'):
            s=sample();s.update(近期可知='0',原ETA=str(2**64-1),新ETA=str(2**64-1),原抵扣='0',新抵扣='0')
            self.assertIsNone(validate_rate(s,block(s,flags))['新ETA秒'])
            s['新抵扣']='1'
            with self.assertRaises(ValueError):validate_rate(s,block(s,flags))

    def test_at_threshold_still_zero_with_incomplete_window(self):
        s=sample();s.update(RC='1024',十秒增量='18',近期可知='0',原ETA='0',新ETA='0',
            起点='95000000000',前起点='95000000000')
        self.assertEqual(validate_rate(s,block(s,'1'))['新ETA秒'],0)
        s['新ETA']=str(2**64-1)
        with self.assertRaises(ValueError):validate_rate(s,block(s,'1'))

    def test_actual_archived_reset_window_regression(self):
        path=Path('/home/data/xwz/rrzc-baselines/joint-sst-pressure-overlap-v1-20261020-results/overlap/overlap/zenfs.run.log')
        if not path.exists():self.skipTest('真实归档不可用，不伪造实验日志')
        blocks={};found=False
        for line in path.read_text().splitlines():
            if 'V1影子候选：' in line:
                b=fields(line,'V1影子候选：');blocks[b['轮次'],b['Zone']]=b
            if '近期ETA对照：' not in line:continue
            r=fields(line,'近期ETA对照：');b=blocks[r['轮次'],r['Zone']]
            if r['轮次']=='3' and r['Zone']=='4026531840':
                result=validate_rate(r,b)
                self.assertEqual(b['窗口标志'],'1')
                self.assertGreater(int(r['十秒增量']),0)
                self.assertLess(int(r['快照'])-int(r['起点']),10*10**9)
                self.assertIsNone(result['原ETA秒']);found=True
        self.assertTrue(found,'未找到导致首轮离线核验失败的真实记录')


if __name__=='__main__':unittest.main()
