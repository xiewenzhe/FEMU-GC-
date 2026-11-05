"""Real tiny QCOW2 lifecycle tests; no VM, authentication or production images."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from image_lifecycle import ImageLease, MAX_CHECKPOINTS, MAX_TEMPORARY, systemd_user_pam

QEMU_IMG = Path(__file__).resolve().parents[3] / 'build-femu/qemu-img'


@unittest.skipUnless(QEMU_IMG.is_file(), '缺少qemu-img，不能验证真实镜像操作')
class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='rrzc-image-lifecycle-')
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.base = self.root / 'images'
        self.base.mkdir()
        self.parent = self.base / 'base.qcow2'
        self.create(self.parent)
        self.archive = self.root / 'evidence.bin'
        self.archive.write_bytes(b'verified archived evidence')
        self.sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def create(self, image, parent=None):
        args = [str(QEMU_IMG), 'create', '-f', 'qcow2']
        if parent:
            args += ['-F', 'qcow2', '-b', str(parent)]
        subprocess.run(args + [str(image), '8M'], check=True, capture_output=True)

    def lease(self, name='experiment', *, checkpoint=False, create=True, parent=None):
        out = self.root / name
        out.mkdir()
        lease = ImageLease(self.base, self.base / (name + '.qcow2'), parent or self.parent,
                           out, QEMU_IMG, checkpoint=checkpoint)
        lease.reserve()
        if create:
            self.create(lease.image, lease.parent)
            lease.created(lease.image, lease.parent)
        return lease

    def finish(self, lease, **kwargs):
        fields = dict(archived=True, guest_exited=True)
        fields.update(kwargs)
        lease.finish(self.archive, self.sha, **fields)

    def test_archived_stopped_temporary_deleted(self):
        lease = self.lease()
        self.finish(lease)
        self.assertFalse(lease.image.exists())
        self.assertTrue(self.parent.exists())
        self.assertTrue(self.archive.exists())
        self.assertEqual(json.loads(lease.entry.read_text())['status'], 'deleted')
        self.assertEqual(json.loads((lease.output / '镜像生命周期.json').read_text())['archive_sha256'], self.sha)

    def test_checkpoint_retained(self):
        lease = self.lease(checkpoint=True)
        self.finish(lease)
        self.assertTrue(lease.image.exists())
        self.assertEqual(lease.data['status'], 'checkpoint')

    def test_incomplete_archive_retained(self):
        lease = self.lease()
        self.finish(lease, archived=False)
        self.assertTrue(lease.image.exists())
        self.assertEqual(lease.data['status'], 'retained-incomplete')

    def test_live_guest_retained(self):
        lease = self.lease()
        self.finish(lease, guest_exited=False)
        self.assertTrue(lease.image.exists())

    def test_bad_archive_hash_refused(self):
        lease = self.lease()
        self.archive.write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, '归档哈希'):
            self.finish(lease)
        self.assertTrue(lease.image.exists())

    def test_same_account_open_descriptor_refused(self):
        lease = self.lease()
        with lease.image.open('rb'):
            with self.assertRaisesRegex(RuntimeError, '独占租约'):
                self.finish(lease)
        self.assertTrue(lease.image.exists())

    def test_protected_systemd_pam_identity_is_narrow(self):
        proc_root = self.root / 'proc'
        pam = proc_root / '22'
        parent = proc_root / '21'
        pam.mkdir(parents=True)
        parent.mkdir()
        (pam / 'comm').write_text('(sd-pam)\n')
        (pam / 'cmdline').write_bytes(b'(sd-pam)\0')
        (pam / 'status').write_text('Name:\t(sd-pam)\nPPid:\t21\n')
        (parent / 'comm').write_text('systemd\n')
        (parent / 'cmdline').write_bytes(b'/lib/systemd/systemd\0--user\0')
        self.assertTrue(systemd_user_pam(pam))
        (parent / 'cmdline').write_bytes(b'/lib/systemd/systemd\0')
        self.assertFalse(systemd_user_pam(pam))

    def test_child_dependency_refused(self):
        lease = self.lease()
        self.create(self.base / 'child.qcow2', lease.image)
        with self.assertRaisesRegex(RuntimeError, '子依赖'):
            self.finish(lease)
        self.assertTrue(lease.image.exists())

    def test_hdd_symlink_child_dependency_refused(self):
        lease = self.lease()
        child = self.root / 'archived-child.qcow2'
        self.create(child, lease.image)
        (self.base / 'child.qcow2').symlink_to(child)
        with self.assertRaisesRegex(RuntimeError, '子依赖'):
            self.finish(lease)
        self.assertTrue(lease.image.exists())

    def test_temporary_parent_refused(self):
        lease = self.lease()
        with self.assertRaisesRegex(RuntimeError, '不能作为后续父镜像'):
            self.lease('next', parent=lease.image)

    def test_checkpoint_limit(self):
        for i in range(MAX_CHECKPOINTS):
            self.lease(str(i), checkpoint=True)
        with self.assertRaisesRegex(RuntimeError, '保留数已达'):
            self.lease('overflow', checkpoint=True)

    def test_unfinished_temporary_limit(self):
        for i in range(MAX_TEMPORARY):
            self.lease(str(i))
        with self.assertRaisesRegex(RuntimeError, '保留数已达'):
            self.lease('overflow')

    def test_released_temporary_frees_capacity(self):
        for i in range(MAX_TEMPORARY + 1):
            lease = self.lease(str(i))
            self.finish(lease)

    def test_symlink_replacement_refused(self):
        lease = self.lease()
        original = self.root / 'original.qcow2'
        lease.image.rename(original)
        lease.image.symlink_to(original)
        with self.assertRaisesRegex(RuntimeError, '普通文件'):
            self.finish(lease)
        self.assertTrue(original.exists())

    def test_inode_replacement_refused(self):
        lease = self.lease()
        lease.image.rename(self.root / 'old.qcow2')
        self.create(lease.image, self.parent)
        with self.assertRaisesRegex(RuntimeError, '身份变化'):
            self.finish(lease)

    def test_creation_failure_releases_reservation(self):
        lease = self.lease(create=False)
        lease.cancel_uncreated()
        self.assertEqual(lease.data['status'], 'not-created')

    def test_preexisting_legacy_image_never_registered_or_deleted(self):
        path = self.base / 'legacy.qcow2'
        self.create(path, self.parent)
        with self.assertRaisesRegex(RuntimeError, '已存在'):
            self.lease('legacy')
        self.assertTrue(path.exists())

    def test_unreadable_dependency_refused(self):
        lease = self.lease()
        (self.base / 'bad.qcow2').write_bytes(b'QFI\xfb broken')
        with self.assertRaises(subprocess.CalledProcessError):
            self.finish(lease)
        self.assertTrue(lease.image.exists())

    def test_run_hooks_and_explicit_builder_retention(self):
        import ast
        root = Path(__file__).resolve().parent
        fixed = (root / 'run_tail_ablation_fixed_pair.py').read_text()
        self.assertIn('self.image_created(image, self.parent_image)', fixed)
        for name in ('run_dbbench_parallel_host.py', 'run_gc_path_host.py'):
            tree = ast.parse((root / name).read_text())
            functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'finish']
            self.assertTrue(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and
                                n.func.attr == 'finish_image' for f in functions for n in ast.walk(f)))
        builder = (root / 'run_all_blocks_host.py').read_text()
        self.assertIn('retain_image=True', builder)

    def exercise_finish(self, cls):
        # SSH/SCP are replaced, but tar extraction, hashing and image removal are real.
        lease = self.lease()
        run = object.__new__(cls)
        run.output = lease.output
        run.target = '/home/femu/experiment'
        run.tag = 'test'
        run.deployed = True
        run.socket = None
        run.state = {'当前客机PID': None}
        run.image_lease = lease
        archive = run.output / 'experiment-test.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(self.archive, arcname='experiment/evidence.bin')
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        run.ssh = lambda command, *args: sha + '  archive' if command.startswith('sha256sum') else ''
        run.scp = lambda: ['scp']
        run.close_guest = lambda: None
        run.record = lambda **fields: run.state.update(fields)

        def command(argv, *args):
            if argv[0] == 'tar':
                subprocess.run(argv, check=True, capture_output=True)
            return ''

        run.command = command
        run.finish()
        self.assertTrue((run.output / 'experiment/evidence.bin').exists())
        self.assertTrue(json.loads((run.output / '阶段归档.json').read_text())['完整'])
        self.assertEqual(run.state['镜像处理']['status'], 'deleted')
        self.assertFalse(lease.image.exists())

    def test_dbbench_real_finish_integration(self):
        from run_dbbench_parallel_host import DbRun
        self.exercise_finish(DbRun)

    def test_gc_rr_real_finish_integration(self):
        from run_gc_path_host import GcRun
        self.exercise_finish(GcRun)


if __name__ == '__main__':
    unittest.main()
