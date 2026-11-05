"""新模型实际接线验收、旧入口隔离及部署收尾测试；合成日志不是设备结果。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from analyze_window_credit import audit_scores
from analyze_joint_opportunity import fields
from replay_expected_rr import oracle,SECOND,SCALE
from test_persistent_credit_host import persistent_fixture,log
import run_expected_rr_host as host
from run_rr_fair_long_v2_host import CollectedRun


def expected_fixture():
    v=8*2**20
    predicted=oracle((128,v,128*2**20,1000,240,480,7,100*SECOND,1024,3,
        1,128,3,v,0,2*SECOND,0,2*SECOND,1,16,SECOND))
    lines=[]
    for line in persistent_fixture():
        for tag in ('窗口收益成本','统一成本'):
            if tag+'：' in line:
                r=fields(line.split('，原因=',1)[0],tag+'：')
                r.update(风险可知=predicted[0],ETA=predicted[1],预计留存=predicted[2],
                    RR抵扣=predicted[3],净复制=predicted[4])
                line=log(tag,**r)
        lines.append(line)
    lines.append(log('期望RR配置',请求=1,启用=1,版本=1,概率尺度=SCALE,先选Greedy=0,泊松假设未校准=1))
    lines.append(log('期望RR成本',轮次=1,Zone=128,版本=1,ETA=predicted[1],预计留存=predicted[2],
        概率可计算=predicted[7],需求概率十亿分数=predicted[8],原持续读抵扣=predicted[9],
        新抵扣=predicted[3],净复制=predicted[4],原因='合成边界，不是实测'))
    return lines


class ExpectedHostTest(unittest.TestCase):
    def test_due_rr_does_not_compare_unused_growth_of_short_circuit_observer(self):
        v=8*2**20;lines=[]
        for line in expected_fixture():
            for tag in ('V1影子候选','近期ETA对照','窗口收益成本','统一成本','持续读收益成本','期望RR成本'):
                if tag+'：' not in line:continue
                r=fields(line.split('，原因=',1)[0],tag+'：')
                if tag=='V1影子候选':r['当前RC']=1024
                elif tag=='近期ETA对照':
                    r.update(RC=1024,原ETA=0,新ETA=0,近期可知=0,近期增量=0,间隔=0,原抵扣=v,新抵扣=v)
                elif tag in ('窗口收益成本','统一成本'):
                    r.update(风险可知=1,ETA=0,预计留存=v,RR抵扣=v,净复制=0)
                    if tag=='窗口收益成本':r.update(读间隔=0,支撑窗口=0)
                elif tag=='持续读收益成本':
                    r.update(平均ETA=0,近期ETA=2**64-1,修正ETA=0,近期可知=1,近期增量=0,
                        近期间隔=SECOND,原抵扣=v,修正抵扣=v)
                else:r.update(ETA=0,预计留存=v,概率可计算=1,需求概率十亿分数=SCALE,
                    原持续读抵扣=v,新抵扣=v,净复制=0)
                line=log(tag,**r)
            lines.append(line)
        result=audit_scores(lines,'window',persistent=True,expected_rr=True)
        self.assertEqual(result[0]['RR抵扣'],v);self.assertEqual(result[0]['ETA'],0)
        # 当前需求仍严查概率与复制，不能用附带诊断差异放过评分篡改。
        for tag,old,new in (('期望RR成本：','需求概率十亿分数=1000000000','需求概率十亿分数=999999999'),
            ('持续读收益成本：','修正抵扣=8388608','修正抵扣=8388607'),
            ('持续读收益成本：','近期间隔=1000000000','近期间隔=3000000000'),
            ('统一成本：','净复制=0','净复制=1')):
            bad=[s.replace(old,new) if tag in s else s for s in lines]
            with self.subTest(tag=tag,old=old),self.assertRaises(ValueError):
                audit_scores(bad,'window',persistent=True,expected_rr=True)

    def test_explicit_expected_mode_required_and_new_cost_recomputed(self):
        lines=expected_fixture();result=audit_scores(lines,'window',persistent=True,expected_rr=True)
        self.assertEqual(len(result),1)
        self.assertGreater(result[0]['RR抵扣'],audit_scores(persistent_fixture(),'window',persistent=True)[0]['RR抵扣'])
        with self.assertRaises(ValueError):audit_scores(lines,'window',persistent=True)
        with self.assertRaises(ValueError):audit_scores(lines,'window',expected_rr=True)
        with self.assertRaises(ValueError):audit_scores(lines,'greedy',persistent=True,expected_rr=True)
        with self.assertRaises(ValueError):audit_scores(persistent_fixture(),'window',persistent=True,expected_rr=True)

    def test_missing_actual_configuration_or_diagnostics_is_rejected(self):
        for marker in ('期望RR配置：','期望RR成本：','持续读收益成本：','窗口收益成本：','近期ETA对照：'):
            with self.subTest(marker=marker),self.assertRaises(ValueError):
                audit_scores([s for s in expected_fixture() if marker not in s],'window',persistent=True,expected_rr=True)

    def test_probability_cost_identity_and_configuration_tampering_rejected(self):
        for marker,old,new in (
            ('期望RR配置：','启用=1','启用=0'),('期望RR配置：','概率尺度=1000000000','概率尺度=1000000'),
            ('期望RR成本：','需求概率十亿分数=','需求概率十亿分数=9'),
            ('期望RR成本：','新抵扣=','新抵扣=9'),('期望RR成本：','预计留存=','预计留存=9'),
            ('期望RR成本：','原持续读抵扣=','原持续读抵扣=9'),
            ('统一成本：','净复制=','净复制=9'),('近期ETA对照：','前块=4','前块=5'),
            ('近期ETA对照：','前代际=3','前代际=4')):
            lines=[s.replace(old,new) if marker in s else s for s in expected_fixture()]
            with self.subTest(marker=marker,old=old),self.assertRaises(ValueError):
                audit_scores(lines,'window',persistent=True,expected_rr=True)

    def test_probability_quantization_uses_exact_logged_byte_accounts(self):
        lines=expected_fixture();entry=next(fields(s.split('，原因=',1)[0],'期望RR成本：') for s in lines if '期望RR成本：' in s)
        units=int(entry['需求概率十亿分数'])+1;credit=int(entry['预计留存'])*units//SCALE
        adjusted=[]
        for line in lines:
            for tag in ('期望RR成本','窗口收益成本','统一成本'):
                if tag+'：' in line:
                    r=fields(line.split('，原因=',1)[0],tag+'：')
                    if tag=='期望RR成本':r.update(需求概率十亿分数=units,新抵扣=credit)
                    else:r['RR抵扣']=credit
                    r['净复制']=8*2**20-credit;line=log(tag,**r)
            adjusted.append(line)
        self.assertEqual(audit_scores(adjusted,'window',persistent=True,expected_rr=True)[0]['RR抵扣'],credit)

    def test_guest_flags_build_checks_and_original_wrapper_isolated(self):
        for name in ('build_expected_rr_guest.sh','run_expected_rr_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/name)],check=True)
        wrapper=(ROOT/'tests/run_expected_rr_guest.sh').read_text()
        for text in ('ZENFS_RRZC_EXPECTED_RR="$enabled"','ZENFS_RRZC_PAIR_GREEDY="$greedy"',
            'RRZC_JOINT_TRACE=1','ZENFS_RR_FAIR_QUEUE=1','ZENFS_RRZC_RECLAIM_BYTES=0'):
            self.assertIn(text,wrapper)
        self.assertNotIn('GC_START',wrapper)
        self.assertNotIn('ZENFS_RRZC_EXPECTED_RR',(ROOT/'tests/run_persistent_credit_guest.sh').read_text())
        build=(ROOT/'tests/build_expected_rr_guest.sh').read_text()
        self.assertIn('make DEBUG_LEVEL=1',build)
        self.assertIn('--reverse --check',build)
        self.assertIn('旧评分、RR或旧客户端身份变化',build)
        self.assertTrue(all(p.is_file() for p in host.FILES))
        self.assertEqual(host.ExpectedRun.finish,CollectedRun.finish)
        self.assertEqual(host.ExpectedRun.close_guest,CollectedRun.close_guest)

    def test_business_start_failure_still_archives_and_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'同库身份.json').write_text('{}')
            run=host.ExpectedRun(SimpleNamespace(label='joint-expected-unit'),None,root,{})
            with patch.object(run,'start',side_effect=RuntimeError('合成启动错误')),patch.object(run,'finish') as finish:
                with self.assertRaisesRegex(RuntimeError,'合成启动错误'):run.arm('retire','expected')
                finish.assert_called_once_with(root/'retire-expected','/home/femu/rrzc-joint-results/retire')

    def test_original_probe_results_recompute_without_changing_old_interpretation(self):
        if not host.MANIFEST.is_file():self.skipTest('真实父采证不可用')
        result=host.checked_source()
        self.assertIn('librocksdb_debug.a',result)
        self.assertEqual(host.CASES,('burst-stopped','eligible-cost','retire'))
        self.assertEqual(host.MODES,('greedy','expected'))


if __name__=='__main__':unittest.main()
