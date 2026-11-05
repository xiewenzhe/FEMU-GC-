"""辅助目录诊断的身份与复制边界；合成文件不作为实验结果。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import aux_tmpfs_guest as m


class AuxTmpfsTest(unittest.TestCase):
    def fixture(self,folder):
        root=Path(folder);mount=root/m.NAME;mount.mkdir()
        (root/'source/aux').mkdir(parents=True)
        saved=root/'逻辑输入/aux';saved.mkdir(parents=True)
        (saved/'文件').write_text('相同的辅助输入')
        (root/'source/aux/旧文件').write_text('保留原始内容')
        return root,mount,saved

    def test_each_arm_restores_input_and_preserves_previous_memory_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            root,mount,saved=self.fixture(folder);old=root/'source/aux'
            with patch.object(m,'mounted',return_value=mount):
                for tag in ('p1','p2','g1','g2'):
                    result=root/tag;result.mkdir()
                    m.restore_aux(root,result,saved,old)
                    self.assertEqual(m.files(old),m.files(saved))
                    self.assertTrue(json.loads((result/'辅助输入核验.json').read_text())['通过'])
                    (old/'文件').write_text(tag)
            self.assertEqual((mount/'g1/文件').read_text(),'g1')
            self.assertEqual((root/'g2/回放前辅助文件/文件').read_text(),'g1')
            self.assertEqual((root/'p1/回放前辅助文件/旧文件').read_text(),'保留原始内容')

    def test_foreign_aux_link_is_rejected_without_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            root,mount,saved=self.fixture(folder);old=root/'source/aux'
            (old/'旧文件').unlink();old.rmdir();old.symlink_to(saved)
            result=root/'g1';result.mkdir()
            with patch.object(m,'mounted',return_value=mount):
                with self.assertRaisesRegex(AssertionError,'不属于本轮'):m.restore_aux(root,result,saved,old)
            self.assertTrue(old.is_symlink());self.assertEqual((saved/'文件').read_text(),'相同的辅助输入')

    def test_file_inventory_rejects_unregistered_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'链接').symlink_to('/tmp')
            with self.assertRaisesRegex(AssertionError,'符号链接'):m.files(root)

    def test_finish_preserves_hash_and_transfers_archive_ownership_before_unmount(self):
        with tempfile.TemporaryDirectory() as folder:
            root,mount,saved=self.fixture(folder)
            mount.chmod(0o700);(mount/'g1').mkdir();(mount/'g1/日志').write_text('真实内容的合成替身')
            calls=[]
            real_chown=m.os.chown
            def chown(path,uid,gid):
                calls.append(('账号',str(path)));real_chown(path,uid,gid)
            def command(argv,check):
                self.assertTrue(any(kind=='账号' for kind,_ in calls))
                calls.append(('卸载',argv[0]))
            with patch.object(m,'guard'),patch.object(m,'mounted',return_value=mount),\
                 patch.object(m.os,'chown',side_effect=chown),\
                 patch.object(m.subprocess,'run',side_effect=command),patch.object(m.os.path,'ismount',return_value=False):
                m.finish(root)
            dest=root/'辅助盘归档'
            self.assertEqual(m.files(dest),m.files(mount))
            self.assertEqual(len([c for c in calls if c[0]=='账号']),3)
            self.assertTrue(json.loads((root/'辅助盘收尾.json').read_text())['已卸载'])


if __name__=='__main__':unittest.main()
