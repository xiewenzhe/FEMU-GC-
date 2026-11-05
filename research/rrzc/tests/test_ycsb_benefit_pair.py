"""合成摘要与监督器分支测试，不运行客机，不将合成数字作为实验结果。"""
import copy
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import run_ycsb_benefit_pair_host as host
import analyze_ycsb_benefit_pair as analysis
from analyze_ycsb_retention import audit


def fixture():
    row=dict(验收通过=True,模式='greedy',计划业务操作数=1200000,无WAL实验=True,
        软件清单SHA256='a'*64,请求轨迹SHA256='b'*64,最新值清单SHA256='c'*64,
        ZC重置=5,RR重置=3,总迁移MiB=80,ZC迁移MiB=30,RR迁移MiB=50,
        应急单区次数=0,空间紧急次数=0,实际改选次数=0,
        调用延迟={op:dict(次数=600000,平均微秒=100,p95微秒=200,p99微秒=300) for op in ('READ','UPDATE')},
        吞吐量=dict(平均ops每秒=990,配置限速ops每秒=1000,限速计划=''),
        分阶段维护={phase:dict(ZC重置=5,RR重置=3,ZC迁移MiB=30,RR迁移MiB=50) for phase in ('业务',)},
        收益预算诊断={})
    other=copy.deepcopy(row);other['模式']='benefit'
    return dict(greedy=row,benefit=other)


class BenefitPairTests(unittest.TestCase):
    def test_dynamic_audit_requires_explicit_opt_in(self):
        with self.assertRaises(ValueError):audit(Path('/没有设备数据'),'benefit')
        with self.assertRaises(ValueError):audit(Path('/没有设备数据'),'greedy',benefit_budget=True)

    def test_zero_switch_and_negative_result_are_preserved(self):
        arms=fixture();out=analysis.compare_results(arms)
        self.assertFalse(out['发生实际模型改选'])
        arms['benefit'].update(实际改选次数=1,RR重置=2,总迁移MiB=100)
        out=analysis.compare_results(arms)
        self.assertTrue(out['发生实际模型改选'])
        self.assertEqual(out['相对Greedy差值']['总迁移MiB'],20)
        self.assertEqual(out['相对Greedy差值']['RR重置'],-1)

    def test_identity_and_rate_mismatch_rejected(self):
        for key in ('软件清单SHA256','请求轨迹SHA256','最新值清单SHA256'):
            arms=fixture();arms['benefit'][key]='d'*64
            with self.subTest(key=key),self.assertRaises(ValueError):analysis.compare_results(arms)
        arms=fixture();arms['benefit']['吞吐量']['配置限速ops每秒']=2000
        with self.assertRaises(ValueError):analysis.compare_results(arms)

    def test_invalid_data_or_missing_throughput_rejected(self):
        for key,value in (('验收通过',False),('计划业务操作数',1),('吞吐量',None),('无WAL实验',False)):
            arms=fixture();arms['benefit'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):analysis.compare_results(arms)

    def test_small_test_only_changes_label_and_comment(self):
        old=(HERE/'check_ycsb_locality_wide_guest.sh').read_text().splitlines()
        new=(HERE/'check_ycsb_benefit_pair_guest.sh').read_text().splitlines()
        self.assertEqual([s.replace('locality-wide-20261019','benefit-pair-20261019') for s in old if not s.startswith('#')],
            [s for s in new if not s.startswith('#')])

    def test_public_runtime_flags_and_workload_unchanged(self):
        old=(HERE/'run_ycsb_locality_wide_guest.sh').read_text()
        new=(HERE/'run_ycsb_benefit_pair_guest.sh').read_text()
        def flags(text):return dict(re.findall(r'\b(ZENFS_[A-Z0-9_]+)=([^\s\\;]+)',text))
        a,b=flags(old),flags(new)
        for name in ('ZENFS_RRZC_BENEFIT_BUDGET_MODE','ZENFS_RRZC_COST_RANK','ZENFS_RRZC_RETENTION_MODE'):
            a.pop(name,None);b.pop(name,None)
        self.assertEqual(a,b)
        self.assertIn('ZENFS_RRZC_COST_RANK=0',new)
        self.assertIn('ZENFS_RRZC_RETENTION_MODE=observe',new)
        self.assertEqual(host.MODES,('greedy','benefit'))
        self.assertEqual(host.PARENT.name,'benefit-device-20261019-build.qcow2')
        self.assertEqual(host.FILES[2].name,'workload-locality-wide-zns.properties')

    def test_early_start_failure_still_runs_cleanup(self):
        run=object.__new__(host.BenefitPair);run.record=Mock()
        run.start=Mock(side_effect=RuntimeError('合成启动错误'))
        run.capture_diagnostics=Mock(side_effect=RuntimeError('合成采证错误'))
        run.finish_stage=Mock()
        with tempfile.TemporaryDirectory(prefix='rrzc-pair-unit-') as directory,\
             patch.object(host,'OUTPUT',Path(directory)),patch.object(host,'resources'):
            with self.assertRaises(RuntimeError):run.arm('greedy',Path(directory),{},[])
            self.assertTrue((Path(directory)/'greedy/运行异常.json').is_file())
            self.assertTrue((Path(directory)/'greedy/现场采证异常.json').is_file())
        run.finish_stage.assert_called_once()


if __name__=='__main__':unittest.main()
