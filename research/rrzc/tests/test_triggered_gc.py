"""编译选区适配并对真实部署源做补丁往返；不冒称已部署或完成设备实验。"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from triggered_gc_patch import make_patch, sources

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('/home/data/xwz/rrzc-baselines/joint-all-blocks-v4-20261025-results/源码')


class TriggeredGcTest(unittest.TestCase):
    def test_compiled_selection_boundaries(self):
        with tempfile.TemporaryDirectory(prefix='rrzc-triggered-gc-') as folder:
            binary = Path(folder) / 'check'
            subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=undefined', '-I' + str(ROOT / 'model'),
                            str(ROOT / 'tests/test_triggered_gc.cc'), '-o', str(binary)], check=True)
            result = subprocess.check_output([str(binary)], text=True)
            self.assertIn('安全边界通过', result)

    def test_actual_source_patch_roundtrip(self):
        if not SOURCE.is_dir():
            self.skipTest('没有实际部署源，不能声称完整接线检查通过')
        before = [(SOURCE / ('after-' + n)).read_text()
                  for n in ('fs_zenfs.cc', 'rrzc_direct_zenfs.inc')]
        patch = make_patch(*before)
        self.assertEqual(patch, (ROOT / 'zenfs-triggered-gc.patch').read_text())
        with tempfile.TemporaryDirectory(prefix='rrzc-triggered-source-') as folder:
            target = Path(folder)
            (target / 'fs').mkdir()
            for name in ('fs_zenfs.cc', 'rrzc_direct_zenfs.inc'):
                shutil.copyfile(SOURCE / ('after-' + name), target / 'fs' / name)
            change = target / 'change.patch'
            change.write_text(patch)
            for args in (['--check'], [], ['--reverse', '--check'], ['--reverse']):
                subprocess.run(['git', 'apply', *args, str(change)], cwd=target, check=True)
            for name, expected in zip(('fs_zenfs.cc', 'rrzc_direct_zenfs.inc'), before):
                self.assertEqual((target / 'fs' / name).read_text(), expected)

    def test_wrong_or_duplicate_source_rejected(self):
        with self.assertRaisesRegex(ValueError, '尚未接入'):
            sources('', '')
        with self.assertRaisesRegex(ValueError, '唯一匹配'):
            sources('const bool all_blocks_enabled=1;\n', '')


if __name__ == '__main__':
    unittest.main()
