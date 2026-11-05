"""真实tar复现aux不可遍历场景，拒绝不受控链接及路径；不启动客机。"""
from pathlib import Path
import subprocess
import shlex
import tarfile
import tempfile
import unittest
from greedy_admission_archive import archive_command,extract


class AdmissionArchiveTest(unittest.TestCase):
    def test_real_tar_excludes_inaccessible_aux_and_preserves_evidence(self):
        _,command=archive_command('/home/femu/joint-controlled-test')
        self.assertIn("--exclude='g1/aux'",command)
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);guest=root/'guest';(guest/'g1').mkdir(parents=True)
            (guest/'g1/run.log').write_text('真实证据文件')
            (guest/'g1/aux').symlink_to('/root/不可访问的测试目录')
            backup=guest/'g1/回放前辅助文件/rrzc-dbbench';backup.mkdir(parents=True)
            (backup/'LOCK').write_text('备份锁文件');(backup/'LOG').write_text('无需准入采集的辅助日志')
            archive=guest/'g1-admission.tar.gz'
            # 执行生产打包命令本身，避免测试遗漏实际包装器生成的合法目录。
            actual=command.replace('/home/femu/joint-controlled-test',shlex.quote(str(guest)))
            subprocess.run(actual,shell=True,check=True)
            destination=root/'out';destination.mkdir();extract(archive,destination)
            self.assertEqual((destination/'g1/run.log').read_text(),'真实证据文件')
            self.assertFalse((destination/'g1/aux').is_symlink())
            with self.assertRaises(ValueError):extract(archive,destination)

    def test_unexpected_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);archive=root/'bad.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                entry=tarfile.TarInfo('g1/aux');entry.type=tarfile.SYMTYPE;entry.linkname='/root'
                tar.addfile(entry)
            with self.assertRaises(ValueError):extract(archive,root)


if __name__=='__main__':unittest.main()
