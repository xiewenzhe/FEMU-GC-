"""预测验证的身份、Reset与删失边界；合成测试不代表实验收益。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_direct_outcomes import analyze, records, validate_prediction

SECOND = 10**9


def block(line=1, seconds=0, rc=4000, **changes):
    return dict(统计准入='1', 当前RC=str(rc), 快照纳秒=str((100+seconds)*SECOND),
                设备会话='1/2', 设备代际='3', 块编号='5', PPA='0/2/1/7',
                代际起点纳秒='0', 行号=line, **changes)


class DirectOutcomesTest(unittest.TestCase):
    def test_observed_crossing_is_interval_not_exact_time(self):
        start = block()
        r = validate_prediction(start, [block(2, 5, 4050), block(3, 9, 4100)], [])
        self.assertEqual(r['结论'], '窗口内已观察同块达阈值')
        self.assertEqual((r['实际达阈值下界秒'], r['实际达阈值上界秒']), (5, 9))

    def test_crossing_after_window_is_not_always_late(self):
        r = validate_prediction(block(), [block(2, 12, 4100)], [])
        self.assertIn('无法判定', r['结论'])
        r = validate_prediction(block(), [block(2, 10, 4050), block(3, 12, 4100)], [])
        self.assertIn('仍未达阈值', r['结论'])

    def test_maximum_identity_change_is_not_same_block_growth(self):
        for key in ('设备会话', '设备代际', '块编号', 'PPA', '代际起点纳秒'):
            later = block(3, 9, 4200)
            later[key] = '不同'
            self.assertIn('不能拿最大RC差', validate_prediction(block(), [later], [])['结论'])

    def test_reset_and_migration_censor_before_next_observation(self):
        for reason in ('Reset删失', '迁移删失', '退役删失'):
            r = validate_prediction(block(), [block(3, 9, 4200)], [(2, reason)])
            self.assertEqual(r['结论'], reason)

    def test_no_followup_is_not_prediction_failure_or_saved_rr(self):
        self.assertIn('无法验证', validate_prediction(block(), [], [])['结论'])
        self.assertEqual(validate_prediction(block(), [], [(2, 'Reset删失')])['结论'], 'Reset删失')

    def test_unknown_statistics_cannot_confirm_forecast(self):
        later = block(3, 9, 4200)
        later['统计准入'] = '0'
        self.assertIn('未知', validate_prediction(block(), [later], [])['结论'])

    def test_same_identity_counter_regression_is_error(self):
        with self.assertRaisesRegex(ValueError, 'RC回退'):
            validate_prediction(block(), [block(2, 1, 10)], [])

    def test_copy_failure_still_censors_and_retirement_parses_suffix(self):
        lines = ['V1后续迁移复制：文件编号=4，源Zone=1，目标Zone=2，复制成功=0',
                 'V1后续文件退役：文件编号=4，文件名=/x，最后链接已删除，元数据持久化成功']
        _, _, _, _, changes, copies, retired = records(lines)
        self.assertEqual(changes['1'][0][0], 1)
        self.assertEqual(changes['2'][0][0], 1)
        self.assertEqual(retired['4'], [2])
        self.assertEqual(copies[0]['复制成功'], '0')

    def test_copy_cause_uses_thread_not_last_global_event(self):
        lines = [
            '2026/10/20-00:00:00.000000 7 RR 开始：Zone 1，达阈值物理块 1',
            '2026/10/20-00:00:00.000001 8 GC 选区汇总：检查空闲比例=10%',
            '2026/10/20-00:00:00.000002 7 V1后续迁移复制：文件编号=1，源Zone=1，目标Zone=2，复制成功=1',
            '2026/10/20-00:00:00.000003 8 V1后续迁移复制：文件编号=2，源Zone=3，目标Zone=4，复制成功=1',
            '2026/10/20-00:00:00.000004 7 RR 完成：Zone 1 已重置',
            '2026/10/20-00:00:00.000005 7 V1后续迁移复制：文件编号=1，源Zone=2，目标Zone=5，复制成功=1']
        copies = records(lines)[5]
        self.assertEqual([r['维护类型'] for r in copies], ['RR', 'ZC', '未知'])

    def test_real_old_log_remains_health_invalid(self):
        path = Path('/home/data/xwz/rrzc-baselines/direct-spare-v4-20261020-results/direct/20261017/zenfs.run.log')
        if not path.is_file():
            self.skipTest('没有旧原始日志，不伪造真实诊断')
        result = analyze(path.read_text())
        self.assertFalse(result['RR健康验收']['验收通过'])
        self.assertIsNone(result['已证明避免RR次数'])
        self.assertEqual(result['决策轮数'], 71)
        self.assertEqual(sum(r['与空间最少不同'] for r in result['选择明细']), 2)
        switched = [r for r in result['选择明细'] if r['与空间最少不同']]
        self.assertEqual(switched[0]['后续同文件复制字节'], 33184549)
        self.assertEqual(switched[1]['后续同文件复制字节'], 0)


if __name__ == '__main__':
    unittest.main()
