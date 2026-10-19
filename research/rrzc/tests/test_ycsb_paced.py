"""限速试次的配置隔离回归；不代表设备实验结果。"""
from pathlib import Path
import sys
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import run_ycsb_paced_host as run


def properties(name):
    return dict(row.split('=',1) for row in (HERE/name).read_text().splitlines()
                if row and not row.startswith('#') and '=' in row)


class PacedTest(unittest.TestCase):
    def test_only_request_rate_changes(self):
        old=properties('workloada-zns-nowal.properties')
        new=properties('workloada-zns-nowal-paced.properties')
        self.assertEqual(old.pop('limit.ops'),'4000')
        self.assertEqual(new.pop('limit.ops'),'1000')
        self.assertEqual(old,new)
        self.assertEqual(new['recordcount'],'655360')
        self.assertEqual(new['operationcount'],'2400000')

    def test_wrapper_keeps_common_and_model_flags(self):
        old=(HERE/'run_ycsb_ready_fallback_guest.sh').read_text()
        new=(HERE/'run_ycsb_paced_guest.sh').read_text()
        flags=lambda text:[row for row in text.splitlines() if 'ZENFS_' in row]
        self.assertEqual(flags(old),flags(new))
        self.assertIn('limit.ops=1000',new)
        self.assertIn('verify_ycsb',new)

    def test_no_source_or_binary_build(self):
        text=(HERE/'build_ycsb_paced_guest.sh').read_text()
        self.assertNotIn('git apply',text)
        self.assertNotIn('make DEBUG_LEVEL',text)
        self.assertNotIn('g++ ',text)
        self.assertIn('sha256sum -c',text)
        self.assertIn('nowal-first nowal-second',text)

    def test_fresh_isolated_images(self):
        self.assertEqual(run.PARENT.name,'ycsb-ready-fallback-20261017-build.qcow2')
        self.assertEqual(run.PORT,'8096')
        self.assertNotEqual(run.PARENT,run.BUILD_IMAGE)
        self.assertIn('ycsb-paced-',run.LABEL)


if __name__=='__main__':unittest.main()
