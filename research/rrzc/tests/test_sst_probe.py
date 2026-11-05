"""先导入口与合成请求边界；不把合成轨迹当作真实YCSB结果。"""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tests'))
from sst_probe_trace import check
from analyze_sst_probe import latency, experiment_size, gc_coverage
from run_sst_probe_host import business_modes


class SstProbeTest(unittest.TestCase):
    def trace(self, start, overlap):
        data = b''
        rows = [('I', i) for i in range(8)]+[('R', 0), ('R', 1), ('U', start), ('U', start+1)]
        for op, key in rows:
            data += op.encode()+struct.pack('<I',24)+f'user{key:020d}'.encode()+bytes(8)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'trace'; path.write_bytes(data)
            r = check(path,8,2,4,start,4)
            self.assertEqual(r['实际读写交集键数'],2 if overlap else 0)
            with self.assertRaises(ValueError):
                check(path,8,2,5,start,4)
            path.write_bytes(data[:-1])
            with self.assertRaises(ValueError):
                check(path,8,2,4,start,4)

    def test_overlap_is_explicit_not_relaxing_legacy_checker(self):
        self.trace(0, True)
        self.trace(2, False)

    def test_invalid_range_rejected(self):
        for start, count in ((7,2),(-1,4),(0,1)):
            with self.assertRaises(ValueError):
                check('/不存在的合成轨迹',8,2,4,start,count)

    def test_explicit_pressure_size_does_not_change_default_or_smoke(self):
        self.assertEqual(experiment_size('disjoint')[0],327680)
        self.assertEqual(experiment_size('disjoint',491520),(491520,400000,4096,4096,65536))
        self.assertEqual(experiment_size('overlap',491520)[3],0)
        self.assertEqual(experiment_size('smoke',491520),(8192,8192,64,64,2048))
        with self.assertRaises(ValueError):
            experiment_size('disjoint',655360)

    def test_single_mode_keeps_smoke_and_rejects_overwrite(self):
        self.assertEqual(business_modes(['disjoint']),['smoke','disjoint'])
        self.assertEqual(business_modes(['disjoint','overlap']),['smoke','disjoint','overlap'])
        for modes in ([],['disjoint','disjoint'],['其他']):
            with self.assertRaises(RuntimeError):
                business_modes(modes)

    def test_gc_entry_is_not_a_candidate_or_successful_cleaning(self):
        text=('GC 检查：空闲比例=20%，进入选区=1\n'
              'GC 选区汇总：无效比例门槛=100%\n'
              '统一决策：轮次=1，实际存在=0，实际Zone=0\n')
        r=gc_coverage(text)
        self.assertEqual(r['进入GC检查数'],1)
        self.assertEqual(r['存在原合格候选轮数'],0)
        self.assertEqual(r['多个安全候选轮数'],0)
        self.assertEqual(r['原无效比例门槛分布'],{'100%':1})
        self.assertNotIn('ZC次数',r)

    def test_multiple_safe_candidates_not_confused_with_emergency_recovery(self):
        text=('统一决策：轮次=1，实际存在=1，实际Zone=128\n'
              'V1影子候选：轮次=1，Zone=128，原候选资格=1，统计准入=1\n'
              'V1影子候选：轮次=1，Zone=256，原候选资格=1，统计准入=1\n'
              '统一成本：轮次=1，Zone=128，安全=1\n'
              '统一成本：轮次=1，Zone=256，安全=1\n'
              '近期ETA对照：轮次=1，Zone=128，新ETA=9000000000\n'
              '空间紧急回收决策：实际Zone=512\n'
              '公共单区恢复决策：存在=1，实际Zone=768\n')
        r=gc_coverage(text)
        self.assertEqual(r['多个原合格候选轮数'],1)
        self.assertEqual(r['多个安全候选轮数'],1)
        self.assertEqual(r['多安全候选且含近期风险轮数'],1)
        self.assertEqual(r['空间应急决策数'],1)
        self.assertEqual(r['公共单区恢复选择数'],1)

    def test_real_histogram_fields_required(self):
        line = ('0 sec: 4 operations; [READ: Count=2 Avg=10 95=12 99=14 99.9=15 99.99=16] '
                '[UPDATE: Count=2 Avg=20 95=22 99=24 99.9=25 99.99=26]')
        r = latency(line,4)
        self.assertEqual(r['UPDATE']['p99.99微秒'],26)
        with self.assertRaises(ValueError):
            latency(line.replace('99.99=26','未知=26'),4)

    def test_scripts_and_isolated_switches(self):
        for name in ('build_sst_probe_guest.sh','run_sst_probe_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/name)],check=True)
        wrapper = (ROOT/'tests/run_sst_probe_guest.sh').read_text()
        for flag in ('ZENFS_RRZC_RECENT_ETA=1','ZENFS_RRZC_ETA_OBSERVE=1',
                     'ZENFS_RRZC_DIRECT=1','ZENFS_RRZC_RECLAIM_BYTES=0',
                     '4294967296','134217728','verify_ycsb'):
            self.assertIn(flag,wrapper)
        self.assertNotIn('GC_START',wrapper)
        self.assertLess(wrapper.index("printf 'mq-deadline"),wrapper.index('"$target/sst_device_probe"'))
        self.assertLess(wrapper.index('"$target/sst_device_probe"'),wrapper.index('zenfs mkfs'))
        build = (ROOT/'tests/build_sst_probe_guest.sh').read_text()
        self.assertIn('core/ycsbc.cc',build)
        self.assertIn('rocksdb/rocksdb_db.cc',build)
        self.assertIn('librocksdb_debug.a',build)
        self.assertIn('make --trace',build)
        self.assertIn("grep -F 'ZENFS_RRZC_RECENT_ETA'",build)
        self.assertNotIn('git apply',build)

    def test_make_trace_keeps_actual_command_for_quiet_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'Makefile'
            path.write_text('all:\n\t@printf "LD librocksdb_debug.a\\n"\n')
            text=subprocess.check_output(['make','--trace'],cwd=tmp,text=True)
            self.assertIn('printf "LD librocksdb_debug.a',text)


if __name__ == '__main__':
    unittest.main()
