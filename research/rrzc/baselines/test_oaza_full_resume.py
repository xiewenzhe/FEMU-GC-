"""Full literal verification must reuse v9, retaining its original cost variant."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / 'common'))
import run_oaza_literal_full_host as literal
from analyze_oaza_literal_resume import rounds_from_native


class LiteralResumeTest(unittest.TestCase):
    def test_gc_scope_excludes_following_rr_and_empty_stat_is_not_reset_event_count(self):
        lines = [
            'frame one', 'OAZA-GC source batch: zone=10 files=1 selected_set_unchanged=1',
            'Zone 重置诊断：Zone=10，状态=成功，主机代际=0/1',
            'ZC 统计：轮次 1，本轮迁移 60 字节，本轮重置 0 个 Zone',
            'OAZA-GC source batch: zone=30 files=1 selected_set_unchanged=1',
            'Zone 重置诊断：Zone=30，状态=成功，主机代际=0/1',
            'frame two', 'OAZA-GC source batch: zone=20 files=1 selected_set_unchanged=1',
            'Zone 重置诊断：Zone=20，状态=成功，主机代际=1/2',
            'ZC 统计：轮次 2，本轮迁移 50 字节，本轮重置 1 个 Zone']
        def frame(line, zone, valid):
            item = dict(zone=zone, valid=valid, capacity=100)
            return dict(line=line, candidates=[item], inferred_from_printed_costs=[item])
        rows = rounds_from_native(lines, {'frames': [frame(1, 10, 60), frame(7, 20, 50)]})
        self.assertTrue(all(row['completed'] for row in rows))
        self.assertEqual(rows[0]['summary']['source_empty_count_at_return'], 0)
        self.assertEqual(len(rows[0]['successful_source_reset_events']), 1)
        self.assertEqual([r['zone'] for row in rows for r in row['source_batches']], [10, 20])

    def test_aborted_last_round_has_no_completed_copy_total(self):
        item = dict(zone=10, valid=60, capacity=100)
        rows = rounds_from_native(['frame', 'OAZA-GC source batch: zone=10 files=1 selected_set_unchanged=1'],
            {'frames': [dict(line=1, candidates=[item], inferred_from_printed_costs=[item])]})
        self.assertFalse(rows[0]['completed']); self.assertIsNone(rows[0]['summary'])

    def test_rejects_build_without_batch_adaptation(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / 'overlay_identity.json').write_text(json.dumps(dict(method='oaza_gc',
                recovery_bootstrap={'policy_formulas_unchanged': True})))
            with mock.patch.object(literal.common, 'admit_build', return_value=(folder, {})):
                with self.assertRaises(KeyError): literal.admit_reused_build(folder)

    def test_literal_is_selected_without_rebuild_or_repeating_adapted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); prior = root / 'prior'; output = root / 'output'; output.mkdir()
            (prior / 'oaza-adapted').mkdir(parents=True)
            (prior / 'oaza-adapted/阶段归档.json').write_text(json.dumps({'业务成功': True}))
            with mock.patch.object(literal, 'PREVIOUS', prior), \
                 mock.patch.object(literal, 'admit_reused_build', return_value=(None, {'binary_sha256': 'known'})), \
                 mock.patch.object(literal.common, 'closed_archive', return_value=({'标签': 'adapted'}, None)), \
                 mock.patch.object(literal.common, 'digest', return_value='known'), \
                 mock.patch.object(literal.common, 'CalibrationRun'), \
                 mock.patch.object(literal.common, 'run_arm') as run, \
                 mock.patch.object(literal.common.native, 'execute') as build:
                literal.execute(output, None)
            build.assert_not_called(); run.assert_called_once()
            self.assertEqual(run.call_args.args[3:6], ('oaza-literal', 'oaza_gc', 'literal'))
            plan = json.loads((output / 'plan.json').read_text())
            self.assertFalse(plan['new_build']); self.assertFalse(plan['repeat_successful_arms'])
            self.assertEqual((plan['windows'], plan['requests'], plan['GC_threshold']), (12, 2260992, 15))


if __name__ == '__main__': unittest.main()
