"""反压修正严格限于两个配置，既有模型与负载不变；不代表设备验证通过。"""
from pathlib import Path
import sys
import unittest
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from test_ycsb_read_pilot import props
from analyze_ycsb_backpressure import check_options
import run_ycsb_backpressure_host as run


class BackpressureTest(unittest.TestCase):
    def test_only_two_changes_in_both_phases(self):
        for old,new in (('workload-pressure-warmup-zns.properties','workload-pressure-backpressure-zns.properties'),
            ('workload-read-latest-zns.properties','workload-read-backpressure-zns.properties')):
            before,after=props(old),props(new)
            self.assertEqual({k for k in before.keys()|after.keys() if before.get(k)!=after.get(k)},
                {'rocksdb.level0_slowdown_writes_trigger','rocksdb.level0_stop_writes_trigger'})
            self.assertEqual(after['rocksdb.level0_slowdown_writes_trigger'],'4')
            self.assertEqual(after['rocksdb.level0_stop_writes_trigger'],'8')

    def test_actual_options_must_not_be_old_or_mixed(self):
        good='Options.level0_file_num_compaction_trigger: 2\nOptions.level0_slowdown_writes_trigger: 4\nOptions.level0_stop_writes_trigger: 8'
        check_options(good)
        for bad in ('',good.replace(': 8',': 36'),good+'\nOptions.level0_stop_writes_trigger: 36'):
            with self.assertRaises(ValueError):check_options(bad)

    def test_no_core_patch_or_compiler(self):
        text=(HERE/'build_ycsb_backpressure_guest.sh').read_text()
        for forbidden in ('git apply','make DEBUG_LEVEL','make -W','g++'):
            self.assertNotIn(forbidden,text)
        self.assertIn('sha256sum -c "$target/parent.software.sha256"',text)
        self.assertIn('compaction_started',text)

    def test_stopped_build_parent_not_failed_business_disk(self):
        self.assertEqual(run.PARENT.name,'ycsb-overlap-20261018-build.qcow2')
        self.assertNotEqual(run.PARENT,run.BUILD_IMAGE)
        self.assertEqual(run.PORT,'8096')
        text=(HERE/'run_ycsb_backpressure_host.py').read_text()
        self.assertIn('Path(f\'/proc/{pid}/cmdline\')',text)
        self.assertIn('run.collect(output,RESULT)',text)
        self.assertNotIn('kill -9',text)

    def test_old_target_is_default_and_new_one_explicit(self):
        text=(HERE/'run_ycsb_overlap_guest.sh').read_text()
        self.assertIn('RRZC_RUN_TARGET:-$directory/overlap-20261018',text)
        self.assertIn('$directory/backpressure-20261018',text)


if __name__=='__main__':unittest.main()
