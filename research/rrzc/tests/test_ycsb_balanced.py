"""只验证新读写比例的登记和入口边界，不冒充设备结果。"""
from pathlib import Path
import sys
import unittest
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
from analyze_ycsb_retention import independent_profile


def props(name):
    return dict(s.split('=',1) for s in (HERE/name).read_text().splitlines()
                if '=' in s and not s.startswith('#'))


class BalancedTest(unittest.TestCase):
    def test_only_request_proportions_changed(self):
        old=props('workload-independent-zns.properties')
        new=props('workload-balanced-zns.properties')
        self.assertEqual({k for k in old.keys()|new.keys() if old.get(k)!=new.get(k)},
                         {'readproportion','updateproportion'})
        self.assertTrue(independent_profile(new,1200000,'50/50'))

    def test_old_audit_not_silently_widened(self):
        old=props('workload-independent-zns.properties')
        new=props('workload-balanced-zns.properties')
        self.assertTrue(independent_profile(old,1200000))
        self.assertFalse(independent_profile(new,1200000))
        self.assertFalse(independent_profile(old,1200000,'50/50'))
        self.assertFalse(independent_profile(new,600000,'50/50'))
        with self.assertRaises(ValueError):independent_profile(new,1200000,'60/40')

    def test_all_mechanism_flags_unchanged(self):
        old=(HERE/'run_ycsb_migration_spare_guest.sh').read_text()
        new=(HERE/'run_ycsb_balanced_guest.sh').read_text()
        self.assertEqual([s for s in old.splitlines() if 'ZENFS_' in s],
                         [s for s in new.splitlines() if 'ZENFS_' in s])
        for check in ('operationcount=1200000','readproportion=0.5','updateproportion=0.5'):
            self.assertIn("grep -Fxq '"+check+"'",new)
        self.assertIn('migration-spare-20261019/build.exit',new)


if __name__=='__main__':unittest.main()
