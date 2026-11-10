"""Isolation/tamper tests on temporary files, without a VM or experiment disk."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import isolation


class IsolationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rrzc-related-isolation-')
        self.root = Path(self.temporary.name)
        self.patch_root = patch.object(isolation, 'ROOT', self.root)
        self.patch_root.start()
        (self.root / 'AGENTS.md').write_text('temporary test project instructions\n')
        self.source = self.root / 'frozen'
        fs = self.source / '新版源码'
        fs.mkdir(parents=True)
        (fs / 'io_zenfs.h').write_text('// known frozen source\n')
        (fs / 'fs_zenfs.cc').write_text('// known frozen implementation\n')
        self.binary = self.root / 'original-program'
        self.binary.write_bytes(b'ORIGINAL PROGRAM: must not be executed or changed')
        self.protected = self.root / 'hw/femu/protected.c'
        self.protected.parent.mkdir(parents=True)
        self.protected.write_text('// existing uncommitted code\n')
        self.untracked = self.root / 'research/rrzc/model/untracked.h'
        self.untracked.parent.mkdir(parents=True)
        self.untracked.write_text('// existing untracked header\n')
        files = {p.name: isolation.digest(p) for p in fs.iterdir()}
        isolation.write_json(self.source / '副本身份.json', dict(
            新源码=files, 原程序=str(self.binary),
            原程序SHA256=isolation.digest(self.binary), 原库SHA256='0' * 64))
        delivery = {str(p.relative_to(self.source)): isolation.digest(p)
                    for p in self.source.rglob('*') if p.is_file()}
        isolation.write_json(self.source / '交付文件SHA256.json', delivery)
        self.output = self.root / 'workspaces/first'

    def tearDown(self):
        self.patch_root.stop()
        self.temporary.cleanup()

    def prepare(self):
        return isolation.prepare(self.output, self.source)

    def test_independent_copies_protect_untracked_files_and_original_binary(self):
        old_binary = self.binary.read_bytes()
        report = self.prepare()
        self.assertTrue(report['complete'])
        self.assertFalse(report['algorithms_implemented'])
        self.assertFalse(report['guest_started'])
        proof = json.loads((self.output / '原机制保护清单.json').read_text())
        self.assertIn('research/rrzc/model/untracked.h', proof)
        nodes = [self.source / '新版源码/io_zenfs.h'] + [
            self.output / m / 'src/plugin/zenfs/fs/io_zenfs.h' for m in isolation.METHODS]
        self.assertEqual(len({(p.stat().st_dev, p.stat().st_ino) for p in nodes}), 4)
        self.assertEqual(self.binary.read_bytes(), old_binary)
        self.assertTrue(isolation.verify(self.output)['passed'])

    def test_refuses_overwrite_without_changing_existing_copy(self):
        self.prepare()
        manifest = (self.output / '准备完成.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '不覆盖'):
            self.prepare()
        self.assertEqual((self.output / '准备完成.json').read_bytes(), manifest)

    def test_tampered_source_is_rejected_before_output_is_created(self):
        (self.source / '新版源码/io_zenfs.h').write_text('// changed\n')
        with self.assertRaisesRegex(ValueError, '身份不符'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_old_program_tamper_is_rejected_before_preparation(self):
        self.binary.write_bytes(b'not the registered program')
        with self.assertRaisesRegex(ValueError, '原可运行程序身份不符'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_copy_tamper_does_not_modify_other_methods_or_original(self):
        self.prepare()
        original = (self.source / '新版源码/io_zenfs.h').read_bytes()
        path = self.output / 'caza/src/plugin/zenfs/fs/io_zenfs.h'
        path.write_text('// intentional tamper\n')
        with self.assertRaisesRegex(ValueError, '身份不符'):
            isolation.verify(self.output)
        for method in ('oaza_gc', 'coldmap'):
            self.assertEqual((self.output / method / 'src/plugin/zenfs/fs/io_zenfs.h').read_bytes(),
                             original)
        self.assertEqual((self.source / '新版源码/io_zenfs.h').read_bytes(), original)

    def test_existing_mechanism_tamper_is_detected(self):
        self.prepare()
        self.untracked.write_text('// external change after freeze\n')
        report = isolation.verify(self.output)
        self.assertFalse(report['passed'])
        self.assertTrue(report['source_trees_valid'])
        self.assertFalse(report['protected_source_unchanged'])
        self.assertEqual([row['path'] for row in report['protected_changes']],
                         ['research/rrzc/model/untracked.h'])

    def test_symlink_in_source_manifest_is_rejected(self):
        path = self.source / '新版源码/io_zenfs.h'
        data = path.read_bytes()
        path.unlink()
        other = self.root / 'link-target'
        other.write_bytes(data)
        path.symlink_to(other)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            self.prepare()

    def test_hardlink_replacement_is_rejected(self):
        self.prepare()
        path = self.output / 'caza/src/plugin/zenfs/fs/io_zenfs.h'
        path.unlink()
        path.hardlink_to(self.output / 'coldmap/src/plugin/zenfs/fs/io_zenfs.h')
        with self.assertRaisesRegex(ValueError, '硬链接'):
            isolation.verify(self.output)

    def test_unsafe_manifest_paths_and_source_destination_overlap(self):
        for name in ('../escape', '/absolute', 'a/../escape', 'a//b', './a', 'a\\b'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, '路径非法'):
                isolation.safe_child(self.root, name)
        with self.assertRaisesRegex(ValueError, '重叠'):
            isolation.prepare(self.source / 'inside-source', self.source)
        with self.assertRaisesRegex(ValueError, '已有源码目录'):
            isolation.prepare(self.protected.parent / 'new-output', self.source)

    def test_no_readiness_claim_without_implementation_or_complete_preparation(self):
        self.prepare()
        state_path = self.output / 'coldmap/方法状态.json'
        state = json.loads(state_path.read_text())
        self.assertFalse(state['runnable'])
        self.assertFalse(state['full_build_passed'])
        state['runnable'] = True
        state_path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, '状态被改写'):
            isolation.verify(self.output)
        (self.output / '准备完成.json').unlink()
        with self.assertRaises(FileNotFoundError):
            isolation.verify(self.output)

    def test_unregistered_source_or_build_product_is_rejected(self):
        self.prepare()
        path = self.output / 'oaza_gc/src/plugin/zenfs/fs/unknown.h'
        path.write_text('// unexpected include\n')
        with self.assertRaisesRegex(ValueError, '混入未登记'):
            isolation.verify(self.output)
        path.unlink()
        (self.output / 'oaza_gc/build/unverified.o').write_bytes(b'unknown object')
        with self.assertRaisesRegex(ValueError, '构建产物'):
            isolation.verify(self.output)

    def test_shared_build_directory_via_symlink_is_rejected(self):
        self.prepare()
        path = self.output / 'oaza_gc/build'
        path.rmdir()
        path.symlink_to(self.output / 'coldmap/build', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            isolation.verify(self.output)

    def test_upstream_pin_verifies_git_blob_and_worktree_content(self):
        upstream = self.root / 'upstream'
        path = upstream / 'rocksdb/plugin/zenfs/fs/fs_zenfs.cc'
        path.parent.mkdir(parents=True)
        data = b'// frozen upstream file\n'
        path.write_bytes(data)
        blob = isolation.hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        commit = 'a' * 40
        def fake_git(repo, *args):
            self.assertEqual(repo, upstream)
            if args == ('rev-parse', 'HEAD'):
                return (commit + '\n').encode()
            if args == ('status', '--porcelain', '--untracked-files=all'):
                return b''
            if args == ('ls-tree', '-rz', commit):
                return ('100644 blob ' + blob + '\trocksdb/plugin/zenfs/fs/fs_zenfs.cc\0').encode()
            self.fail('unexpected git command: ' + repr(args))
        with patch.object(isolation, 'git', fake_git):
            record = isolation.pin_upstream(upstream, commit)
            self.assertEqual(record['files']['rocksdb/plugin/zenfs/fs/fs_zenfs.cc'],
                             isolation.digest(path))
            with self.assertRaisesRegex(ValueError, 'HEAD未固定'):
                isolation.pin_upstream(upstream, 'b' * 40)
            path.write_bytes(b'// unregistered edit\n')
            with self.assertRaisesRegex(ValueError, 'Git对象不符'):
                isolation.pin_upstream(upstream, commit)


if __name__ == '__main__':
    unittest.main()
