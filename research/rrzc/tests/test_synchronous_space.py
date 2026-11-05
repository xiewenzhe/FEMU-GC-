"""实际线程测试受控空间交接；原筛选正文不能改变。"""
from pathlib import Path
import subprocess
import tempfile
import unittest
from controlled_gc_source import patched,synchronous_space
from target_alloc_source import patched as target_patch,controlled_retry,synchronous_space_retry


class SynchronousSpaceTest(unittest.TestCase):
    def test_real_threads_wait_for_corresponding_completed_maintenance(self):
        root=Path(__file__).parent
        with tempfile.TemporaryDirectory() as folder:
            binary=Path(folder)/'同步交接'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread','-I'+str(root),
                str(root/'test_boundary_sync.cc'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True,timeout=10)

    def test_actual_sources_keep_selection_body_and_only_request_after_failed_retry(self):
        root=Path('/home/data/xwz/rrzc-baselines/joint-controlled-boundary-full-v1-20261027-results/joint-controlled-boundary-full-v1-20261027')
        fs=root/'修复后-fs_zenfs.cc';zbd=root/'修复后-zbd_zenfs.cc'
        if not fs.exists():self.skipTest('部署源码尚未归档')
        native=fs.read_text();changed=synchronous_space(patched(native))
        marker='    if (!superblock_->IsGCEnabled()) continue;'
        self.assertEqual(native.split(marker,1)[1],changed.split(marker,1)[1])
        self.assertNotIn('waiters && now>=next_emergency',changed)
        text=synchronous_space_retry(controlled_retry(target_patch(zbd.read_text())))
        self.assertLess(text.index('boundary_retry.unlock()'),text.index('State().Request()'))
        self.assertLess(text.index('if (allocated_zone != nullptr) {\n              Info(logger_, "分配回退成功'),
            text.index('State().Request()'))
        self.assertEqual(text.count('z->Acquire()'),zbd.read_text().count('z->Acquire()'))


if __name__=='__main__':unittest.main()
