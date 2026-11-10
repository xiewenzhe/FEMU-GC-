"""Recovered metadata must be complete before recovery can wait for GC space."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest import mock

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('bootstrap', BASE / 'common/prepare_recovery_bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec); spec.loader.exec_module(bootstrap)


class RecoveryBootstrapTest(unittest.TestCase):
    def test_forks_leave_original_and_policy_bytes_unchanged(self):
        for method, old, new in [('oaza_gc', 'oaza-gc-v7-20261109', 'oaza-gc-v8-20261109'),
                                 ('coldmap', 'coldmap-v5-20261109', 'coldmap-v6-20261109')]:
            source = BASE / 'workspaces' / old; fork = BASE / 'workspaces' / new
            identity = json.loads((source / 'overlay_identity.json').read_text())
            actual = json.loads((fork / 'overlay_identity.json').read_text())
            changed = []
            for name, digest in identity['source_sha256'].items():
                original = (source / 'src/rocksdb' / name).read_bytes()
                self.assertEqual(bootstrap.sha(original), digest)
                amended = (fork / 'src/rocksdb' / name).read_bytes()
                self.assertEqual(bootstrap.sha(amended), actual['source_sha256'][name])
                if amended != original: changed.append(name)
            self.assertEqual(changed, actual['recovery_bootstrap']['changed'])
            self.assertEqual(set(changed), {'db/db_impl/db_impl_open.cc', 'include/rrzc_caza_bridge.h'})
            text = (fork / 'src/rocksdb/db/db_impl/db_impl_open.cc').read_text()
            recover = text.index('&recovered_seq, &recovery_ctx, can_retry);')
            publish = text.index('rrzc_caza::Bridge().Publish(', recover)
            wal = text.index('impl->CreateWAL(', recover)
            manifest = text.index('impl->LogAndApplyForRecovery(recovery_ctx)', recover)
            self.assertLess(recover, publish); self.assertLess(publish, wal); self.assertLess(publish, manifest)
            self.assertIn('!recovery_ctx.is_new_db_', text)
            self.assertNotIn('impl->impl->', text)
            if method == 'oaza_gc':
                keys = text.index('rrzc_caza::Bridge().RestoreKeys(', recover)
                self.assertLess(keys, publish)
                self.assertEqual(text.count('rrzc.oaza.nonempty.keys.v1'), 1)
                self.assertIn('recovered_version->Ref();', text)
                self.assertIn('recovered_version->Unref();', text)
                self.assertIn('keys != item.second->num_entries', text)
            with self.assertRaises(AssertionError): bootstrap.prepare(source, fork)

    def test_changed_anchor_is_rejected(self):
        with self.assertRaises(AssertionError): bootstrap.once('absent', 'expected', 'replacement')

    def test_source_batch_execution_preserves_selected_policy_and_restores_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = str(Path(temp) / 'order')
            subprocess.run(['g++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=undefined', '-fno-sanitize-recover=all',
                            '-I' + str(BASE / 'oaza_gc'), str(BASE / 'oaza_gc/migration_order_test.cc'),
                            '-o', binary], check=True, capture_output=True, timeout=60)
            subprocess.run([binary], check=True, capture_output=True, timeout=10)
        old = BASE / 'workspaces/oaza-gc-v8-20261109/src/rocksdb'
        new = BASE / 'workspaces/oaza-gc-v9-20261109/src/rocksdb'
        for name in ['include/rrzc_oaza_policy.h', 'plugin/zenfs/fs/rrzc_oaza_selector.inc',
                     'db/db_impl/db_impl_open.cc', 'plugin/zenfs/fs/zbd_zenfs.cc']:
            self.assertEqual((old / name).read_bytes(), (new / name).read_bytes())

    def test_full_failure_reproduction_requires_functional_regression(self):
        spec = importlib.util.spec_from_file_location('bootstrap_full', BASE / 'common/run_bootstrap_full_host.py')
        full = importlib.util.module_from_spec(spec); spec.loader.exec_module(full)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); queue = root / 'queue.json'
            queue.write_text(json.dumps(dict(status='failed', jobs=[])))
            with mock.patch.object(full, 'PRIOR_QUEUE', queue), \
                 mock.patch.object(full.common.native, 'execute') as build, \
                 mock.patch.object(full.common, 'run_arm') as run:
                with self.assertRaisesRegex(RuntimeError, 'functional regression failed'):
                    full.execute(root, None)
                build.assert_not_called(); run.assert_not_called()


if __name__ == '__main__': unittest.main()
