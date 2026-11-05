"""只测软件恢复的路径与身份约束；合成文件不是实验收益数据。"""
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_calibration_host as runner
from restore_calibration_guest import digest,restore


class CalibrationRestoreTest(unittest.TestCase):
    def test_budget_tracks_actual_fresh_image(self):
        from run_triggered_gc_host import check_reused_image_budget
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);args=SimpleNamespace(label='joint-controlled-test',fresh_image=True)
            with patch.object(runner,'IMAGE_DIR',root):path=runner.working_image(args)
            self.assertEqual(path,root/'joint-controlled-test-calibration.qcow2')
            path.write_bytes(b'actual')
            run=SimpleNamespace(args_reuse_image=path,max_image_bytes=path.stat().st_blocks*512)
            check_reused_image_budget(run)
            args.fresh_image=False
            self.assertEqual(runner.working_image(args),runner.IMAGE)

    def test_fresh_and_reuse_start_paths_are_explicit(self):
        run=runner.CalibrationRun.__new__(runner.CalibrationRun)
        run.fresh_image=True
        with patch.object(runner.RrRun,'start',return_value='登记新盘') as fresh,\
             patch.object(runner.ReuseRun,'start',return_value='复用') as reuse:
            self.assertEqual(run.start(),'登记新盘');fresh.assert_called_once();reuse.assert_not_called()
        run.fresh_image=False
        with patch.object(runner.ReuseRun,'start',return_value='复用'):
            self.assertEqual(run.start(),'复用')

    def test_restore_refuses_path_outside_guest(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(AssertionError):restore(Path(name))

    def test_archive_members_cannot_escape_restore_directory(self):
        # 真实解包前验证清单；下面使用受控替身路径映射，不接触真实/home/femu。
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);target=root/'实验';target.mkdir()
            payload=target/'恢复文件.tar.gz'
            with tarfile.open(payload,'w:gz') as tar:
                item=tarfile.TarInfo('../逃逸');item.size=0;tar.addfile(item)
            (target/'宿主恢复清单.json').write_text(json.dumps(dict(压缩包SHA256=digest(payload),文件={'../逃逸':'不应读取'})))
            class GuestPath(type(target)):
                @property
                def parent(self):
                    if str(self)==str(target):return Path('/home/femu')
                    return super().parent
            with self.assertRaises(AssertionError):restore(GuestPath(target))
            self.assertFalse((root/'逃逸').exists())


if __name__=='__main__':unittest.main()
