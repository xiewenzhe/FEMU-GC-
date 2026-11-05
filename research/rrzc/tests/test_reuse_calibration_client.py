"""复用入口拒绝身份漂移；合成文件不作为真实数据库实验。"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import reuse_calibration_client as reuse_module


class ReuseCalibrationTest(unittest.TestCase):
    def test_verified_copy_preserves_mode_and_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'previous';target=root/'current';source.mkdir();target.mkdir()
            binary=source/'db_bench';binary.write_bytes(b'validated-client');binary.chmod(0o700)
            obj=target/'fs_all_blocks.o';obj.write_bytes(b'zenfs-object')
            identity=dict(客户端SHA256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                ZenFS对象SHA256=hashlib.sha256(obj.read_bytes()).hexdigest())
            (source/'校准客户端身份.json').write_text(json.dumps(identity))
            plan=dict(客机目录=str(source),文件SHA256={n:hashlib.sha256((source/n).read_bytes()).hexdigest()
                for n in ('db_bench','校准客户端身份.json')})
            (target/'复用诊断客户端.json').write_text(json.dumps(plan))
            def resolved(value):return root if value=='/home/femu' else Path(value)
            with patch.object(reuse_module,'Path',side_effect=resolved):
                reuse_module.reuse(target)
                self.assertEqual((target/'db_bench').stat().st_mode&0o777,0o700)
                self.assertEqual((target/'db_bench').read_bytes(),b'validated-client')
                binary.write_bytes(b'changed-client')
                with self.assertRaisesRegex(AssertionError,'身份变化'):reuse_module.reuse(target)
                self.assertEqual((target/'db_bench').read_bytes(),b'validated-client')

    def test_source_archive_member_check(self):
        from flush_wait_source import old_flush_extracted
        self.assertFalse(old_flush_extracted('librocksdb_debug.a(format.o) /tmp/诊断-flush_job.o (symbol)'))
        self.assertTrue(old_flush_extracted('librocksdb_debug.a(db/flush_job.o) /tmp/a.o (symbol)'))


if __name__=='__main__':unittest.main()
