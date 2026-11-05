"""配对入口与统计边界测试；合成计数仅测试解析，不是设备结果。"""
import copy
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from analyze_ycsb_balanced_pair import compare_results
import run_ycsb_balanced_pair_host as host


def fixture():
    phases = {name: dict(ZC尝试=3, ZC重置=3, RR尝试=2, RR重置=2,
                        ZC迁移MiB=4.0, RR迁移MiB=6.0)
              for name in ('装载及等待', '交界一秒不确定', '业务')}
    row = dict(验收通过=True, 模式='greedy', 计划业务操作数=1200000, 无WAL实验=True,
        软件清单SHA256='a' * 64, 请求轨迹SHA256='b' * 64, 最新值清单SHA256='c' * 64,
        ZC重置=9, RR重置=6, 总迁移MiB=30.0, ZC迁移MiB=12.0, RR迁移MiB=18.0,
        应急单区次数=1, 空间紧急次数=0, 实际改选次数=0, 分阶段维护=phases,
        调用延迟={op: dict(次数=600000, 平均微秒=100.0, p95微秒=150.0, p99微秒=200.0)
                  for op in ('READ', 'UPDATE')})
    other = copy.deepcopy(row)
    other['模式'] = 'retention'
    return dict(greedy=row, retention=other)


class PairEntry(unittest.TestCase):
    def test_only_guest_target_and_mode_guard_changed(self):
        old = (HERE / 'run_ycsb_balanced_guest.sh').read_text()
        expected = old.replace('balanced-20261019', 'balanced-pair-20261019').replace(
            '# 仅改变请求读写比例为50/50；实际Greedy与RR/GC/模型条件均沿用上一轮。',
            '# 读写各半配对：只切换Greedy与留存净收益的实际选择入口。').replace(
            '[[ $# == 1 && $1 == greedy && ! -e $result ]]',
            '[[ $# == 1 && ( $1 == greedy || $1 == retention ) && ! -e $result ]]')
        self.assertEqual((HERE / 'run_ycsb_balanced_pair_guest.sh').read_text(), expected)

    def test_small_test_only_changes_directory(self):
        self.assertEqual((HERE / 'check_ycsb_balanced_pair_guest.sh').read_text(),
            (HERE / 'check_ycsb_balanced_guest.sh').read_text().replace('balanced-20261019', 'balanced-pair-20261019'))

    def test_two_fresh_children_same_build_parent(self):
        self.assertEqual(host.MODES, ('greedy', 'retention'))
        self.assertEqual(host.BalancedPair.parent_image.name, 'ycsb-migration-spare-20261019-build.qcow2')
        self.assertNotEqual(host.OUTPUT, host.PILOT)
        self.assertEqual(host.FILES[2].name, 'workload-balanced-zns.properties')
        source = (HERE / 'run_ycsb_balanced_pair_host.py').read_text()
        self.assertIn('for mode in MODES:', source)
        self.assertIn('self.arm(mode, source, expected)', source)
        self.assertNotIn('self.parent_image =', source)


class PairComparison(unittest.TestCase):
    def test_throughput_present_or_explicitly_unknown(self):
        arms=fixture()
        self.assertIsNone(compare_results(arms)['吞吐量相对Greedyops每秒'])
        for mode,rate in (('greedy',980),('retention',960)):
            arms[mode]['吞吐量']=dict(平均ops每秒=rate,配置限速ops每秒=1000,限速计划='')
        self.assertEqual(compare_results(arms)['吞吐量相对Greedyops每秒'],-20)
        arms['retention']['吞吐量']['配置限速ops每秒']=2000
        with self.assertRaises(ValueError):compare_results(arms)

    def test_zero_switch_not_a_gain_claim(self):
        result = compare_results(fixture())
        self.assertTrue(result['配对核验通过'])
        self.assertFalse(result['发生实际模型改选'])
        self.assertEqual(result['相对Greedy差值']['总迁移MiB'], 0)
        self.assertEqual(result['业务阶段']['greedy']['总迁移MiB'], 10)

    def test_negative_result_is_kept(self):
        arms = fixture()
        arms['retention'].update(实际改选次数=2, 总迁移MiB=35, ZC迁移MiB=20, RR迁移MiB=15)
        result = compare_results(arms)
        self.assertTrue(result['配对核验通过'])
        self.assertTrue(result['发生实际模型改选'])
        self.assertEqual(result['相对Greedy差值']['总迁移MiB'], 5)
        self.assertEqual(result['相对Greedy差值']['RR迁移MiB'], -3)

    def test_identity_mismatches_rejected(self):
        for key in ('软件清单SHA256', '请求轨迹SHA256', '最新值清单SHA256'):
            with self.subTest(key=key):
                arms = fixture()
                arms['retention'][key] = 'd' * 64
                with self.assertRaises(ValueError):
                    compare_results(arms)

    def test_invalid_runs_rejected(self):
        for key, value in (('验收通过', False), ('模式', 'greedy'),
                           ('计划业务操作数', 600000), ('无WAL实验', False)):
            arms = fixture()
            arms['retention'][key] = value
            with self.assertRaises(ValueError):
                compare_results(arms)
        arms = fixture()
        arms['greedy']['实际改选次数'] = 1
        with self.assertRaises(ValueError):
            compare_results(arms)

    def test_latency_delta_and_incomplete_counts(self):
        arms = fixture()
        arms['retention']['调用延迟']['READ']['平均微秒'] = 80
        self.assertEqual(compare_results(arms)['调用延迟相对Greedy微秒']['READ']['平均微秒'], -20)
        arms['retention']['调用延迟']['READ']['次数'] -= 1
        with self.assertRaises(ValueError):
            compare_results(arms)


if __name__ == '__main__':
    unittest.main()
