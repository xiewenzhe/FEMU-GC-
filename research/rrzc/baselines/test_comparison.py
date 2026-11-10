"""Shared workload admission rejects request, content and fresh-device drift."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import struct
import os
import tarfile
import sys
import tempfile
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / 'common'))
import comparison_source as source
import analyze_comparison as analyze
import summarize_comparison as summary
import compact_closed_requests as compact
import finish_comparison_host as finish
import run_comparison_host as controller
import comparison_failure as failure
import gzip
from types import SimpleNamespace


class ComparisonTest(unittest.TestCase):
    def test_recovery_keeps_failed_arm_out_of_completed_and_does_not_repeat_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); monitor = root / 'resume'; monitor.mkdir()
            for arm in ('g2', 'caza', 'oaza-literal'):
                (root / arm).mkdir()
                (root / arm / '阶段归档.json').write_text(json.dumps({'业务成功': arm != 'oaza-literal'}))
            (root / 'comparison-state.json').write_text('{}')
            def reused(args, output, arm, *_):
                state = json.loads((output / 'comparison-state.json').read_text())
                controller.record(output, completed=[*state['completed'], arm])
            proof = dict(binary_sha256='known', passed=False)
            args = SimpleNamespace(resume=root, windows=12, build=root, continue_after_failed_arm=True)
            with mock.patch.object(controller, 'reuse_archived_arm', side_effect=reused) as reuse, \
                 mock.patch.object(controller, 'run_arm', side_effect=lambda a, o, p, arm, m, v: reused(a, o, arm)) as run, \
                 mock.patch.object(controller, 'failure_receipt', return_value=proof), \
                 mock.patch.object(controller, 'admit_build', return_value=(None, {'binary_sha256': 'known'})):
                controller.execute_arms(args, root, monitor, None)
            state = json.loads((root / 'comparison-state.json').read_text())
            self.assertEqual([c.args[2] for c in reuse.call_args_list], ['g2', 'caza'])
            self.assertEqual([c.args[3] for c in run.call_args_list], ['oaza-adapted', 'cold-printed', 'cold-survival'])
            self.assertEqual(state['stage'], 'complete_with_failures')
            self.assertEqual(state['failed_arms'], ['oaza-literal'])
            self.assertNotIn('oaza-literal', state['completed'])
            self.assertEqual(len(state['completed']), 5)
            self.assertFalse(state['all_arms_passed'])

    def test_failure_receipt_rejects_missing_archive_bytes_and_success_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); folder = root / 'case'; folder.mkdir()
            (root / '阶段归档.json').write_text(json.dumps({'业务成功': False}))
            image = root / 'removed.qcow2'
            (root / '镜像生命周期.json').write_text(json.dumps({'status': 'deleted', 'image': str(image)}))
            wanted = dict(arm='oaza-literal', windows=12)
            values = {'run.exit': '-6', 'run.log': 'what(): allocation failure', 'run.zenfs.log': 'ZC 迁移失败：NoSpace',
                      'load.exit': '0', 'comparison_plan.json': json.dumps(wanted), 'comparison_db_bench': 'known'}
            for name, value in values.items(): (folder / name).write_text(value)
            archive = root / 'archive.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                for name in values: tar.add(folder / name, arcname='case/' + name)
            state = {'标签': 'case', '镜像处理': {'archive_sha256': compact.digest(archive)}}
            with mock.patch.object(failure, 'closed_archive', return_value=(state, archive)):
                result = failure.failure_receipt(root, wanted)
                self.assertFalse(result['passed'])
                self.assertTrue(result['archive_integrity_verified'])
                (folder / 'run.log').write_text('changed')
                with self.assertRaises(AssertionError): failure.failure_receipt(root, wanted)
                (folder / 'run.log').write_text(values['run.log'])
                (folder / 'comparison_complete.json').write_text('{}')
                with self.assertRaises(AssertionError): failure.failure_receipt(root, wanted)

    def test_failure_after_successful_business_remains_a_failed_full_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); folder = root / 'case'; folder.mkdir()
            (root / '阶段归档.json').write_text(json.dumps({'业务成功': False}))
            (root / '镜像生命周期.json').write_text(json.dumps({'status': 'deleted', 'image': str(root / 'removed')}))
            wanted = dict(arm='oaza-adapted', windows=12)
            values = {'load.exit': '0', 'run.exit': '0', 'run.log': '应用写批次完成：批次=720',
                      'verify.exit': '1', 'verify.log': 'open error: NoSpace', 'verify.zenfs.log': 'deferred',
                      'phase.txt': 'verify', 'comparison_plan.json': json.dumps(wanted), 'comparison_db_bench': 'known'}
            for name, value in values.items(): (folder / name).write_text(value)
            archive = root / 'archive.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                for name in values: tar.add(folder / name, arcname='case/' + name)
            state = {'标签': 'case', '镜像处理': {'archive_sha256': compact.digest(archive)}}
            with mock.patch.object(failure, 'closed_archive', return_value=(state, archive)):
                proof = failure.failure_receipt(root, wanted)
            self.assertEqual(proof['failed_phase'], 'verify')
            self.assertEqual(proof['business_exit'], 0)
            self.assertFalse(proof['full_workload_verified'])
            self.assertEqual(proof['last_successful_flush_batch'], 720)

    def test_failed_archive_allows_lossless_bins_but_rejects_changed_compressed_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); folder = root / 'case'; folder.mkdir()
            (root / '阶段归档.json').write_text(json.dumps({'业务成功': False}))
            (root / '镜像生命周期.json').write_text(json.dumps({'status': 'deleted', 'image': str(root / 'removed')}))
            wanted = dict(arm='cold-printed', windows=12)
            values = {'load.exit': '0', 'run.exit': '0', 'run.log': '', 'verify.exit': '1',
                      'verify.log': 'open error: NoSpace', 'verify.zenfs.log': '', 'phase.txt': 'verify',
                      'comparison_plan.json': json.dumps(wanted), 'comparison_db_bench': 'known',
                      'requests.bin': 'original request bytes'}
            for name, value in values.items(): (folder / name).write_text(value)
            archive = root / 'archive.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                for name in values: tar.add(folder / name, arcname='case/' + name)
            raw = folder / 'requests.bin'; sha = compact.digest(raw); packed = folder / 'requests.bin.gz'
            with gzip.open(packed, 'wb') as f: f.write(raw.read_bytes())
            (root / '失败二进制无损压缩.json').write_text(json.dumps({'files': [dict(
                name='requests.bin', sha256=sha, compressed_sha256=compact.digest(packed))]}))
            raw.unlink()
            state = {'标签': 'case', '镜像处理': {'archive_sha256': compact.digest(archive)}}
            with mock.patch.object(failure, 'closed_archive', return_value=(state, archive)):
                proof = failure.failure_receipt(root, wanted)
                self.assertFalse(proof['passed']); self.assertEqual(proof['files']['requests.bin'], sha)
                packed.write_bytes(b'changed')
                with self.assertRaises(AssertionError): failure.failure_receipt(root, wanted)

    def test_completion_supervision_never_publishes_failed_or_unverified_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'run'; out = Path(temp) / 'status'; root.mkdir(); out.mkdir()
            state = root / 'comparison-state.json'
            state.write_text(json.dumps(dict(stage='failed', error='original failure', completed=['g2'])))
            with mock.patch.object(finish, 'supervisor_alive', return_value=False), \
                 mock.patch.object(finish, 'verify') as verify:
                finish.execute(root, out, 10)
                verify.assert_not_called()
            proof = json.loads((out / 'completion-state.json').read_text())
            self.assertEqual(proof['stage'], 'stopped_at_controller_failure')
            self.assertFalse(proof['results_claimed'])
            state.write_text(json.dumps(dict(stage='complete_with_failures', completed=['g2'],
                                            failed_arms=['oaza-literal'])))
            with mock.patch.object(finish, 'supervisor_alive', return_value=False), \
                 mock.patch.object(finish, 'verify') as verify:
                finish.execute(root, out, 10)
                verify.assert_not_called()
            proof = json.loads((out / 'completion-state.json').read_text())
            self.assertEqual(proof['stage'], 'completed_with_failed_arms')
            self.assertFalse(proof['full_workload_verified'])
            state.write_text(json.dumps(dict(stage='complete', completed=[])))
            (out / 'dependency-sha256.json').write_text('{}')
            with mock.patch.object(finish, 'supervisor_alive', return_value=False), \
                 mock.patch.object(finish, 'verify', side_effect=AssertionError('bad archive')), \
                 mock.patch.object(finish, 'summarize') as summarize:
                with self.assertRaises(AssertionError): finish.execute(root, out, 10)
                summarize.assert_not_called()
            proof = json.loads((out / 'completion-state.json').read_text())
            self.assertEqual(proof['stage'], 'final_verification_failed')
            self.assertFalse(proof['results_claimed'])

    def test_closed_request_compression_preserves_archive_and_all_alias_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); original = root / 'frozen/p1/请求提交.bin'
            alias = root / '逐臂采证/p1/请求提交.bin'
            original.parent.mkdir(parents=True); alias.parent.mkdir(parents=True)
            data = b'RRZCREQ1' + b'known-request-evidence' * 1000
            original.write_bytes(data); os.link(original, alias)
            archive = root / 'evidence.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar: tar.add(original, arcname='frozen/p1/请求提交.bin')
            archive_sha = compact.digest(archive)
            (root / '阶段归档.json').write_text(json.dumps({'业务成功': True}))
            with mock.patch.object(compact, 'closed_archive', return_value=({'标签': 'frozen'}, archive)), \
                 mock.patch.object(compact, 'closed_no_guest'):
                plan = compact.plan_one(root); result = compact.execute_one(plan)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(compact.digest(archive), archive_sha)
            self.assertFalse(original.exists()); self.assertFalse(alias.exists())
            for path in (original, alias):
                with compact.gzip.open(path.with_suffix('.bin.gz'), 'rb') as f: self.assertEqual(f.read(), data)
            self.assertEqual(original.with_suffix('.bin.gz').stat().st_ino, alias.with_suffix('.bin.gz').stat().st_ino)

    def test_closed_request_plan_rejects_unknown_alias_or_archive_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); original = root / 'frozen/请求提交.bin'; original.parent.mkdir()
            original.write_bytes(b'correct evidence')
            (root / '阶段归档.json').write_text(json.dumps({'业务成功': True}))
            archive = root / 'bad.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                member = tarfile.TarInfo('frozen/请求提交.bin'); member.size = 5
                tar.addfile(member, __import__('io').BytesIO(b'wrong'))
            with mock.patch.object(compact, 'closed_archive', return_value=({'标签': 'frozen'}, archive)):
                with self.assertRaises(AssertionError): compact.plan_one(root)
                os.link(original, root / 'unknown-alias')
                with self.assertRaises(AssertionError): compact.plan_one(root)
            self.assertEqual(original.read_bytes(), b'correct evidence')

    def test_collected_only_request_still_requires_exact_immutable_archive_member(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); copy = root / '逐臂采证/p1/请求提交.bin'; copy.parent.mkdir(parents=True)
            data = b'known archived request bytes'; copy.write_bytes(data)
            (root / '阶段归档.json').write_text(json.dumps({'业务成功': True}))
            archive = root / 'archive.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                m = tarfile.TarInfo('frozen/p1/请求提交.bin'); m.size = len(data)
                tar.addfile(m, __import__('io').BytesIO(data))
            with mock.patch.object(compact, 'closed_archive', return_value=({'标签': 'frozen'}, archive)):
                plan = compact.plan_one(root)
                self.assertEqual(next(iter(plan['groups'].values()))['archive_member'], 'frozen/p1/请求提交.bin')
                copy.write_bytes(b'changed')
                with self.assertRaises(AssertionError): compact.plan_one(root)

    def test_space_summary_distinguishes_finished_padding_from_live_bytes(self):
        zones = [dict(slba=i * 100, wp=i * 100, cap=100, state='EMPTY') for i in range(64)]
        zones[0].update(wp=100, state='FULL')
        zones[1].update(wp=140, state='IMP_OPENED')
        result = summary.zone_metrics({'zones': {'zone_list': zones}})
        self.assertEqual(result['empty_zones'], 62)
        self.assertEqual(result['open_zones'], 1)
        self.assertEqual(result['active_zones'], 1)
        self.assertEqual(result['written_or_finished_bytes'], 140 * 512)
        self.assertEqual(result['writable_tail_bytes'], (62 * 100 + 60) * 512)
        zones[2].update(wp=201)
        with self.assertRaises(AssertionError): summary.zone_metrics({'zones': {'zone_list': zones}})

    def test_all_methods_compile_identical_workload_and_explicit_variants(self):
        for method in source.METHODS:
            text = source.builder(method).decode(); compile(text, '<shared-builder>', 'exec')
            self.assertIn('comparison-client.o', text)
            self.assertIn('comparison-posix', text)
            self.assertIn('original_library_unchanged=digest(library) == before', text)
        self.assertEqual(len(source.ARMS), 6)
        self.assertEqual({v for _, m, v in source.ARMS if m == 'oaza_gc'}, {'literal', 'adapted'})
        self.assertEqual({v for _, m, v in source.ARMS if m == 'coldmap'}, {'printed', 'artifact-survival'})
        self.assertIn('windows<1 || windows>12 || smoke', source.lifetime_header().decode())

    def test_real_request_format_checks_entire_first_full_size_window(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'requests.bin'
            with path.open('wb') as f:
                f.write(b'RRZCREQ1'); counts = [0, 0]
                for n, (write, key) in enumerate(analyze.planned(1)):
                    f.write(struct.pack('<7Q', n, write, counts[write], key, n * 3 + 1, n * 3 + 2, n * 3 + 3))
                    counts[write] += 1
            proof = analyze.request_proof(path, 1)
            self.assertEqual((proof['reads'], proof['writes']), (65536, 122880))
            with path.open('r+b') as f:
                f.seek(8 + 188415 * 56 + 24); f.write(struct.pack('<Q', 999999))
            with self.assertRaises(AssertionError): analyze.request_proof(path, 1)

    def test_fresh_device_comparison_ignores_only_session_and_capture_times(self):
        block = dict(zip(('Zone', '块', '代际', 'RC', '十秒增量', '三十秒增量', 'Channel', 'LUN', 'Plane', '物理块', '标志'), [0] * 11))
        first = dict(zones={'zone_list': [dict(slba=0, wp=0, cap=262144, state='EMPTY')]},
                     blocks=[dict(block, 设备会话=[1, 2], 快照=10, 代际起点=2)], identify={'mor': 7, 'mar': 11}, strict={'启用': 1})
        second = deepcopy(first); second['blocks'][0].update(设备会话=[3, 4], 快照=20, 代际起点=3)
        self.assertEqual(analyze.initial_semantics(first), analyze.initial_semantics(second))
        second['blocks'][0]['RC'] = 1
        self.assertNotEqual(analyze.initial_semantics(first), analyze.initial_semantics(second))
        second['blocks'][0]['RC'] = 0; second['blocks'][0]['物理块'] = 1
        self.assertNotEqual(analyze.initial_semantics(first), analyze.initial_semantics(second))

    def test_prefix_application_audit_retains_actual_flush_completion_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp); requests = list(analyze.planned(1)); batches = {}
            indices = [0, 0]
            for seq, (role, _) in enumerate(requests):
                if role:
                    batch = indices[1] // 2048 + 1
                    count, first, _ = batches.get(batch, (0, seq, seq))
                    batches[batch] = (count + 1, first, seq)
                indices[role] += 1
            indices = [0, 0]
            with (p / '请求提交.bin').open('wb') as api, (p / '应用完成.bin').open('wb') as done:
                api.write(b'RRZCREQ1'); done.write(b'RRZCE201' + struct.pack('<2Q', 4096, 2048))
                for seq, (role, key) in enumerate(requests):
                    start = seq * 10 + 1; end = start + 1
                    api.write(struct.pack('<7Q', seq, role, indices[role], key, start, end, end))
                    batch = indices[1] // 2048 + 1 if role else 0
                    complete = batches[batch][2] * 10 + 5 if role else end
                    done.write(struct.pack('<10Q', seq, role, key, start, start, start, start, end, complete, batch))
                    indices[role] += 1
            lines = ['应用写批次完成：批次=%d，写数=%d，首请求=%d，末请求=%d，Flush开始纳秒=%d，Flush返回纳秒=%d，完成纳秒=%d' %
                     (batch, size, first, last, last * 10 + 3, last * 10 + 4, last * 10 + 5)
                     for batch, (size, first, last) in batches.items()]
            (p / 'run.log').write_text('\n'.join(lines))
            result = analyze.application_audit(p, 1)
            self.assertEqual(result['完成请求数'], 188416)
            self.assertEqual(result['实际同步写批次'], 60)
            with self.assertRaises(ValueError): analyze.application_audit(p, 2)
            # A write acknowledged before its actual batch Flush must still fail.
            with (p / '应用完成.bin').open('r+b') as done:
                done.seek(24 + 64); done.write(struct.pack('<Q', 2))
            with self.assertRaises(ValueError): analyze.application_audit(p, 1)
    def test_cross_arm_rejects_value_or_client_drift_even_when_requests_match(self):
        report = dict(requests={'logical_sha256': 'same'}, client_files={'client.cc': 'one'},
                      generated_input=['1048576', '17'], final_digest=['1048576', '4311744512', '19'])
        initial = dict(zones={'zone_list': []}, blocks=[], identify={}, strict={})
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / 'a'; second = Path(temp) / 'b'; first.mkdir(); second.mkdir()
            for folder in (first, second):
                (folder / 'initial.json').write_text(json.dumps(initial))
                (folder / 'common-options.json').write_text('[]')
            self.assertTrue(analyze.compare(first, second, report, deepcopy(report))['passed'])
            for field in ('requests', 'client_files', 'generated_input', 'final_digest'):
                other = deepcopy(report); other[field] = 'different'
                with self.assertRaises(AssertionError): analyze.compare(first, second, report, other)


if __name__ == '__main__': unittest.main()
