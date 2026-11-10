"""Guard generated helpers: correct method/variant, no false device acceptance."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
import json
import subprocess
import sys
from unittest import mock

BASE = Path(__file__).resolve().parent


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, BASE / relative)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


build = load('paper_build_entry', 'common/run_native_host.py')
device = load('paper_device_entry', 'common/run_device_host.py')
evidence = load('paper_evidence_entry', 'common/verify_paper_runs.py')
queue = load('paper_joint_queue', 'common/run_joint_validation_host.py')


class PaperEntrypointTest(unittest.TestCase):
    def test_queue_authentication_pipe_preserves_value_without_line_terminator(self):
        with tempfile.TemporaryDirectory() as temporary:
            probe = Path(temporary) / 'auth_probe.py'
            probe.write_text("import getpass; assert getpass.getpass() == 'test-value'; print('pipe verified')\n")
            result = subprocess.run([sys.executable, '-c', queue.AUTH_BOOTSTRAP, str(probe)],
                                    input='test-value\n', text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'pipe verified')

    def test_queue_requires_child_exit_and_post_archive_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary)
            (p / '阶段归档.json').write_text(json.dumps({'业务成功': True}))
            status = p / '状态.json'
            status.write_text(json.dumps({'监督进程PID': 1, '标签': 'test', '阶段': 'GC已归档并退出'}))
            with mock.patch.object(queue, 'child_active', return_value=True):
                self.assertFalse(queue.job_complete(p, 'device'))
            with mock.patch.object(queue, 'child_active', return_value=False), mock.patch.object(queue, 'closed_archive'):
                with self.assertRaises(RuntimeError): queue.job_complete(p, 'device')
                status.write_text(json.dumps({'监督进程PID': 1, '标签': 'test', '阶段': '归档哈希、退出和逐笔事件账目通过'}))
                with self.assertRaises(RuntimeError): queue.job_complete(p, 'device')
                for name in ('device-composition-audit.json', 'verify-composition-audit.json', 'physical-mapping-audit.json'):
                    (p / name).write_text(json.dumps({'passed': True}))
                self.assertTrue(queue.job_complete(p, 'device'))

    def test_coldmap_both_factor_directions_are_explicit(self):
        for variant in ('printed', 'artifact-survival'):
            text = device.wrapper('coldmap', variant).decode()
            compile(text, '<generated-coldmap>', 'exec')
            self.assertIn("ZENFS_RELATED_WORK='coldmap'", text)
            self.assertIn('COLDMAP_FACTOR_DIRECTION=' + repr(variant), text)
            self.assertIn("assert 'ColdMap selected: zone=' in log", text)
            self.assertIn("assert 'ColdMap deferred:' not in log", text)

    def test_oaza_keeps_variants_and_requires_actual_gc(self):
        for variant in ('literal', 'adapted', 'prose'):
            text = device.wrapper('oaza_gc', variant).decode()
            compile(text, '<generated-oaza>', 'exec')
            self.assertIn("ZENFS_RELATED_WORK='oaza_gc'", text)
            self.assertIn('OAZA_GC_VARIANT=' + repr(variant), text)
            self.assertIn('gc_device_verified=gc_verified', text)
            self.assertIn("int((target / 'oaza-gc-reset.txt').read_text())", text)
            self.assertIn('int(requested) == expected_quantity', text)
            self.assertNotIn("assert 'CAZA target: stage=overlap' in log", text)

    def test_build_recompiles_added_units_and_keeps_abi_gate(self):
        for method in ('coldmap', 'oaza_gc'):
            text = build.build_helper(method).decode()
            compile(text, '<generated-builder>', 'exec')
            self.assertIn("units += [n for n in identity['changed_units']", text)
            self.assertIn("offsets['original'] == offsets['new']", text)
            self.assertIn("assert digest(library) == before", text)
            self.assertIn('method=' + repr(method), text)

    def test_middle_fixture_is_real_device_dispatch_not_basic_case(self):
        text = build.probe_source('oaza_gc').decode()
        self.assertIn('ExerciseOazaMiddle(env.get(), *fs, output, mode == "verify")', text)
        self.assertIn('test_case != "middle" && test_case != "recovery",', text)
        wrapper = device.wrapper('oaza_gc', 'literal').decode()
        self.assertIn("'fault', 'nospace', 'middle')", wrapper)
        self.assertIn("assert {row['level'] for row in mapping} == {'2', '3'}", wrapper)
        self.assertIn("stages[0]['L2'] == '0'", wrapper)
        self.assertIn("verify-before-write.csv", wrapper)

    def test_coldmap_model_fixture_requires_real_completion_and_simulation(self):
        source = build.probe_source('coldmap').decode()
        self.assertIn('ExerciseColdMapModel(env.get(), *fs, *device', source)
        self.assertIn('test_case != "model" && test_case != "joint",', source)
        wrapper = device.wrapper('coldmap', 'printed').decode()
        compile(wrapper, '<model-wrapper>', 'exec')
        self.assertIn("model['compactions'] == '3'", wrapper)
        self.assertIn("int(model['generated_pivots']) > 0", wrapper)

    def test_joint_fixtures_enable_native_gc_and_require_real_joint_evidence(self):
        cold = device.wrapper('coldmap', 'artifact-survival', 'joint').decode()
        oaza = device.wrapper('oaza_gc', 'literal', 'recovery').decode()
        for text in (cold, oaza):
            compile(text, '<joint-wrapper>', 'exec')
            self.assertIn("if test_case in ('gc', 'joint', 'recovery'): mkfs += ['--enable_gc']", text)
            self.assertIn("logical('device-mapping.csv') == logical('verify-mapping.csv')", text)
        self.assertIn('int(row[3]) > 0 and int(row[4]) >= 2', cold)
        self.assertIn("'OAZA-GC frame:' not in log", oaza)
        self.assertIn("'native_label'] == '0'", oaza)
        self.assertIn('ExerciseColdMapJoint(', build.probe_source('coldmap').decode())
        self.assertIn('logger.get(), true)', build.probe_source('oaza_gc').decode())

    def test_caza_joint_uses_original_policy_and_natural_rr(self):
        source = build.probe_source('caza').decode()
        self.assertIn('ExerciseCazaJoint(', source)
        self.assertNotIn('ExerciseColdMapModel(', source)
        header = build.joint_header('caza').decode()
        self.assertIn('if (!verify_only) ExerciseJointRR(', header)
        wrapper = device.wrapper('caza', 'native', 'joint').decode()
        compile(wrapper, '<caza-joint>', 'exec')
        self.assertIn("ZENFS_RELATED_WORK='caza'", wrapper)
        self.assertNotIn('COLDMAP_FACTOR_DIRECTION=', wrapper)
        self.assertNotIn('OAZA_GC_VARIANT=', wrapper)
        self.assertIn("after['threshold_events']", wrapper)
        compile(build.build_helper('caza').decode(), '<caza-builder>', 'exec')

    def test_mapping_change_requires_committed_copy_chain(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary)
            (p / 'device-mapping.csv').write_text('sst,level,zone,start,length\n1,1,100,110,10\n')
            (p / 'verify-mapping.csv').write_text('sst,level,zone,start,length\n1,1,300,330,10\n')
            first = dict(事件=1, 源Zone=100, 源片段=110, 目标Zone=200, 目标片段=220, 有效复制字节=10, 映射结局='提交')
            second = dict(事件=2, 源Zone=200, 源片段=220, 目标Zone=300, 目标片段=330, 有效复制字节=10, 映射结局='提交')
            self.assertFalse(device.mapping_transition(p, [{'复制明细': []}, {'复制明细': []}])['passed'])
            proof = device.mapping_transition(p, [{'复制明细': [first]}, {'复制明细': [second]}])
            self.assertTrue(proof['passed']); self.assertEqual(len(proof['explained_extent_changes']), 2)
            second['映射结局'] = '未提交／撤销'
            self.assertFalse(device.mapping_transition(p, [{'复制明细': [first]}, {'复制明细': [second]}])['passed'])

    def test_mapping_csv_can_precede_later_committed_gc_without_ignoring_its_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary)
            (p / 'device-mapping.csv').write_text('sst,level,zone,start,length\n1,1,100,110,10\n')
            (p / 'verify-mapping.csv').write_text('sst,level,zone,start,length\n1,1,200,220,10\n')
            first = dict(事件=1, 源Zone=100, 源片段=110, 目标Zone=200, 目标片段=220, 有效复制字节=10, 映射结局='提交')
            later = dict(事件=2, 源Zone=200, 源片段=220, 目标Zone=300, 目标片段=330, 有效复制字节=10, 映射结局='提交')
            proof = device.mapping_transition(p, [{'复制明细': []}, {'复制明细': [first, later]}])
            self.assertTrue(proof['passed'])
            self.assertEqual(proof['committed_copy_interval_end'], 1)
            self.assertEqual(proof['later_committed_copies'], 1)
            self.assertEqual(proof['later_committed_bytes'], 10)
            self.assertFalse(device.mapping_transition(p, [{'复制明细': []}, {'复制明细': [later]}])['passed'])
            first['映射结局'] = '未提交／撤销'
            self.assertFalse(device.mapping_transition(p, [{'复制明细': []}, {'复制明细': [first, later]}])['passed'])

    def test_coldmap_evidence_rejects_wrong_score_victim_and_actual_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary)
            prefix = ('ColdMap candidate: zone=100 valid=10 capacity=100 cold=0.500000000000 score=4.500000000000\n'
                      'ColdMap candidate: zone=200 valid=20 capacity=100 cold=0.500000000000 score=2.000000000000\n')
            suffix = 'ZC 统计：轮次 1，本轮迁移 10 字节，本轮重置 1\n'
            report = {'有效复制字节': 10, '复制明细': [{'源Zone': 100, '映射结局': '提交'}]}
            log = p / 'zenfs-device.log'
            log.write_text(prefix + 'ColdMap selected: zone=100 direction=printed\n' + suffix)
            result = evidence.phase_summary(p, 'device', report)
            self.assertEqual(result['coldmap_score_rounds'][0]['candidates'], 2)
            log.write_text(prefix + 'ColdMap selected: zone=200 direction=printed\n' + suffix)
            with self.assertRaises(AssertionError): evidence.phase_summary(p, 'device', report)
            log.write_text(prefix.replace('score=4.500000000000', 'score=5.500000000000') +
                           'ColdMap selected: zone=100 direction=printed\n' + suffix)
            with self.assertRaises(AssertionError): evidence.phase_summary(p, 'device', report)
            log.write_text(prefix + 'ColdMap selected: zone=100 direction=printed\n' + suffix)
            report['复制明细'][0]['源Zone'] = 200
            with self.assertRaises(AssertionError): evidence.phase_summary(p, 'device', report)

    def test_recovery_cost_is_recomputed_from_keys_bytes_and_restored_hint(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary)
            (p / 'oaza-recovered-keys.csv').write_text(
                'sst,level,keys,smallest,largest,amplification\n'
                '1,1,10,0001,0010,10\n2,1,20,0011,0020,10\n3,0,5,0001,0020,10\n')
            zones = p / 'oaza-recovered-zones.csv'
            zones.write_text('zone,native_label,file_label,valid,capacity,remaining\n100,0,3,100,200,0\n')
            (p / 'device-mapping.csv').write_text('sst,level,zone,start,length\n1,1,100,100,40\n2,1,100,140,60\n')
            log = p / 'zenfs-verify.log'
            prefix = 'OAZA-GC candidate: zone=100 valid=100 capacity=200 label=3 cost='
            log.write_text(prefix + '0.037500000000\nOAZA-GC frame: completed\n')
            self.assertEqual(len(evidence.recovery_costs(p, 'literal')), 1)
            with self.assertRaises(AssertionError): evidence.recovery_costs(p, 'adapted')
            log.write_text(prefix + '0.052500000000\nOAZA-GC frame: completed\n')
            self.assertEqual(len(evidence.recovery_costs(p, 'adapted')), 1)
            log.write_text(prefix + '0.000000000000\nOAZA-GC frame: completed\n')
            with self.assertRaises(AssertionError): evidence.recovery_costs(p, 'adapted')
            log.write_text(prefix + '0.052500000000\nOAZA-GC frame: completed\n')
            zones.write_text('zone,native_label,file_label,valid,capacity,remaining\n100,0,2,100,200,0\n')
            with self.assertRaises(AssertionError): evidence.recovery_costs(p, 'adapted')


if __name__ == '__main__': unittest.main()
