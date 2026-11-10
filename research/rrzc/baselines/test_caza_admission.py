"""Admission regression: linking alone or edited extracted logs cannot authorize device testing."""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from caza.run_device_host import prepare
from caza.run_device_host import HERE


class AdmissionTest(unittest.TestCase):
    def fixture(self, root, success=True):
        source = root / 'source'; source.mkdir(); old = source / 'fixture'; old.mkdir()
        def write(path, value): path.write_text(json.dumps(value))
        binary = old / 'caza_probe'; binary.write_bytes(b'synthetic admission fixture, never executed')
        write(old / 'build_complete.json', dict(passed=True, NDEBUG=True,
              known_failing_member_offsets_match=True,
              binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest()))
        write(old / 'core_abi_check.json', dict(passed=True))
        write(old / 'overlay_identity.json', dict(test_fixture_only=True))
        (old / 'posix-smoke.log').write_text('CAZA posix latest values/checksums/reopen passed\n')
        (old / 'build.log').write_text('posix-smoke passed\n')
        archive = source / 'fixture.tar.gz'
        with tarfile.open(archive, 'x:gz') as tar:
            for path in old.iterdir(): tar.add(path, arcname='fixture/' + path.name)
        write(source / '状态.json', dict(标签='fixture', 当前客机PID=None,
              镜像处理=dict(archive=str(archive), archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())))
        write(source / '阶段归档.json', dict(完整=True, 客机仍存活=False, 业务成功=success))
        return source

    def test_successful_archived_program_and_logs_are_admitted(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = self.fixture(root); payload = root / 'payload.tar.gz'
            result = prepare(source, payload)
            self.assertEqual(len(result['verified_archive_files']), 6)
            with tarfile.open(payload) as tar:
                self.assertIn('caza_probe', tar.getnames())

    def test_linked_program_with_failed_posix_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = self.fixture(root, False); payload = root / 'payload.tar.gz'
            with self.assertRaisesRegex(ValueError, 'POSIX admission'):
                prepare(source, payload)
            self.assertFalse(payload.exists())

    def test_edited_extracted_posix_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = self.fixture(root); payload = root / 'payload.tar.gz'
            (source / 'fixture/posix-smoke.log').write_text('tampered\n')
            with self.assertRaisesRegex(ValueError, 'differs from verified archive'):
                prepare(source, payload)
            self.assertFalse(payload.exists())

    def with_test_sources(self, source, stale=False):
        old = source / 'fixture'
        for name in ('probe.cc', 'rr_probe.h', 'pressure_probe.h', 'nospace_probe.h', 'fault_io.cc'):
            (old / name).write_bytes((HERE / name).read_bytes())
        if stale: (old / 'rr_probe.h').write_text('old unsupported fixture source\n')
        archive = source / 'with-test-sources.tar.gz'
        with tarfile.open(archive, 'x:gz') as tar:
            for path in old.iterdir(): tar.add(path, arcname='fixture/' + path.name)
        state = json.loads((source / '状态.json').read_text())
        state['镜像处理'] = dict(archive=str(archive), archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        (source / '状态.json').write_text(json.dumps(state))

    def test_legacy_receipt_requires_exact_archived_rr_test_source(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = self.fixture(root); self.with_test_sources(source, stale=True)
            payload = root / 'payload.tar.gz'
            with self.assertRaisesRegex(ValueError, 'test-source identity'):
                prepare(source, payload, 'rr')
            self.assertFalse(payload.exists())

    def test_legacy_receipt_with_exact_rr_test_source_is_admitted(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = self.fixture(root); self.with_test_sources(source)
            payload = root / 'payload.tar.gz'; result = prepare(source, payload, 'rr')
            self.assertEqual(len(result['verified_archive_files']), 11)
            with tarfile.open(payload) as tar:
                self.assertEqual(json.load(tar.extractfile('test_case.json'))['case'], 'rr')

    def test_capability_flag_cannot_admit_stale_rr_test_source(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = self.fixture(root)
            path = source / 'fixture/build_complete.json'
            proof = json.loads(path.read_text()); proof['supported_cases'] = ['basic', 'rr']
            path.write_text(json.dumps(proof)); self.with_test_sources(source, stale=True)
            payload = root / 'payload.tar.gz'
            with self.assertRaisesRegex(ValueError, 'test-source identity'):
                prepare(source, payload, 'rr')
            self.assertFalse(payload.exists())

    def test_pressure_requires_successful_archived_pressure_posix_check(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = self.fixture(root)
            (source / 'fixture/posix-pressure-smoke.log').write_text('failed pressure fixture\n')
            self.with_test_sources(source)
            with self.assertRaisesRegex(ValueError, 'pressure POSIX admission'):
                prepare(source, root / 'payload.tar.gz', 'pressure')
            self.assertFalse((root / 'payload.tar.gz').exists())

    def test_gc_admission_verifies_pressure_protocol_and_posix(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = self.fixture(root); old = source / 'fixture'
            (old / 'posix-pressure-smoke.log').write_text('CAZA posix latest values/checksums/reopen passed\n')
            with (old / 'build.log').open('a') as log: log.write('posix-pressure-smoke passed\n')
            self.with_test_sources(source)
            payload = root / 'payload.tar.gz'; prepare(source, payload, 'gc')
            with tarfile.open(payload) as tar:
                self.assertEqual(json.load(tar.extractfile('test_case.json'))['case'], 'gc')

    def test_fault_library_requires_its_own_verified_build_identity(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = self.fixture(root); old = source / 'fixture'
            (old / 'fault_io.so').write_bytes(b'synthetic library fixture, never loaded')
            (old / 'fault-scope.log').write_text('regular files preserved\n')
            path = old / 'build_complete.json'; proof = json.loads(path.read_text())
            proof['fault_library_sha256'] = '0' * 64; path.write_text(json.dumps(proof))
            self.with_test_sources(source)
            with self.assertRaisesRegex(ValueError, 'fault-library admission'):
                prepare(source, root / 'payload.tar.gz', 'fault')
            self.assertFalse((root / 'payload.tar.gz').exists())


if __name__ == '__main__': unittest.main()
