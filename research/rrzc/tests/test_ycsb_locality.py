"""真实上游请求回归与范围校验，不把普通目录测试称为设备收益。"""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
UPSTREAM=HERE.parents[2]/'build-femu/rrzc/ycsb-upstream-20261017'
sys.path[:0]=[str(HERE),str(HERE.parent)]
from check_ycsb_independent_trace import check
from analyze_ycsb_retention import independent_profile


class LocalityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='rrzc-locality-')
        cls.root=Path(cls.temp.name)
        for mode in ('before','after'):
            work=cls.root/mode;core=work/'core';core.mkdir(parents=True)
            for name in ('core_workload.h','core_workload.cc'):
                shutil.copyfile(UPSTREAM/'core'/name,core/name)
            patches=['ycsb-independent-keys.patch']
            if mode=='after':patches.append('ycsb-update-range.patch')
            for patch in patches:
                subprocess.run(['patch','-p1','-i',str(HERE/patch)],cwd=work,check=True,capture_output=True)
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(core),'-I'+str(UPSTREAM/'core'),'-I'+str(UPSTREAM),
                str(HERE/'ycsb_key_trace.cc'),str(core/'core_workload.cc'),
                str(UPSTREAM/'core/acknowledged_counter_generator.cc'),'-o',str(work/'trace')],check=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def trace(self,mode,*args):
        return subprocess.run([str(self.root/mode/'trace'),*args],capture_output=True,
            env={**os.environ,'YCSB_RANDOM_SEED':'20261017'})

    def test_old_paths_byte_identical(self):
        for enabled in ('false','true'):
            for ratio in (('0.8','0.2'),('0.5','0.5')):
                args=('rrzc.independent_keys='+enabled,'readproportion='+ratio[0],'updateproportion='+ratio[1])
                before=self.trace('before',*args)
                self.assertEqual(before.returncode,0,before.stderr)
                for extra in ((),('rrzc.update_key_start=0','rrzc.update_key_count=1024')):
                    after=self.trace('after',*args,*extra)
                    self.assertEqual(after.returncode,0,after.stderr)
                    self.assertEqual(before.stdout,after.stdout)

    def test_disjoint_range_repeats(self):
        args=('rrzc.independent_keys=true','readproportion=0.5','updateproportion=0.5',
            'rrzc.update_key_start=64','rrzc.update_key_count=192')
        result=self.trace('after',*args)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,self.trace('after',*args).stdout)
        reads=[];updates=[]
        for row in result.stdout.decode().splitlines():
            op,key,*_=row.split();n=int(key[4:])
            (reads if op=='R' else updates).append(n)
        self.assertEqual(len(reads)+len(updates),20000)
        self.assertTrue(9500<=len(reads)<=10500)
        self.assertTrue(all(0<=n<64 for n in reads))
        self.assertEqual(set(updates),set(range(64,256)))

    def test_invalid_ranges(self):
        for start,count in (('0','0'),('0','1'),('1024','2'),('1023','2'),('0','1025'),
            ('-1','2'),('1x','2'),('','2'),('0','-1'),('0',''),('0','2x'),
            ('18446744073709551615','2'),('0','18446744073709551616')):
            with self.subTest(start=start,count=count):
                result=self.trace('after','rrzc.independent_keys=true',
                    'rrzc.update_key_start='+start,'rrzc.update_key_count='+count)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(result.stdout,b'')

    def test_wide_business_range_real_generator(self):
        args=('rrzc.independent_keys=true','recordcount=655360','rrzc.read_key_count=4096',
            'readproportion=0.5','updateproportion=0.5','rrzc.update_key_start=4096',
            'rrzc.update_key_count=258048')
        result=self.trace('after',*args)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,self.trace('after',*args).stdout)
        reads=[];updates=[]
        for row in result.stdout.decode().splitlines():
            op,key,*_=row.split();n=int(key[4:])
            (reads if op=='R' else updates).append(n)
        self.assertEqual(len(reads)+len(updates),20000)
        self.assertTrue(reads and updates)
        self.assertTrue(all(0<=n<4096 for n in reads))
        self.assertTrue(all(4096<=n<262144 for n in updates))
        self.assertTrue(any(n>=65536 for n in updates))


class LocalityAuditTest(unittest.TestCase):
    def test_wide_profile_is_separate_and_only_count_changes(self):
        def props(name):
            return dict(s.split('=',1) for s in (HERE/name).read_text().splitlines()
                if '=' in s and not s.startswith('#'))
        old=props('workload-locality-zns.properties');new=props('workload-locality-wide-zns.properties')
        self.assertEqual({k for k in old.keys()|new.keys() if old.get(k)!=new.get(k)}, {'rrzc.update_key_count'})
        self.assertTrue(independent_profile(new,1200000,'50/50',(4096,258048)))
        self.assertFalse(independent_profile(new,1200000,'50/50',(4096,61440)))
        self.assertFalse(independent_profile(old,1200000,'50/50',(4096,258048)))
        self.assertFalse(independent_profile(new,1200000,'50/50'))

    def test_wide_guest_changes_only_directory_and_range(self):
        old=(HERE/'run_ycsb_locality_guest.sh').read_text()
        expected=old.replace('locality-20261019','locality-wide-20261019').replace(
            'workload-locality-zns','workload-locality-wide-zns').replace(
            '$(cat "$nowal/build.exit")','$(cat "$directory/locality-20261019/build.exit")').replace(
            'rrzc.update_key_count=61440','rrzc.update_key_count=258048')
        self.assertEqual((HERE/'run_ycsb_locality_wide_guest.sh').read_text(),expected)
        source=(HERE/'run_ycsb_locality_wide_host.py').read_text()
        self.assertIn("PARENT=IMAGE_DIR/'ycsb-locality-20261019-build.qcow2'",source)
        self.assertIn("吞吐量=result['吞吐量']",source)
        self.assertNotIn("self.start('build')",source)

    def test_guest_keeps_device_and_model_switches(self):
        old=(HERE/'run_ycsb_balanced_guest.sh').read_text()
        new=(HERE/'run_ycsb_locality_guest.sh').read_text()
        self.assertEqual([s for s in old.splitlines() if 'ZENFS_' in s],
            [s for s in new.splitlines() if 'ZENFS_' in s])
        self.assertIn('[[ $# == 1 && $1 == greedy && ! -e $result ]]',new)
        self.assertIn('$(cat "$nowal/build.exit") == 0',new)
        build=(HERE/'build_ycsb_locality_guest.sh').read_text()
        self.assertIn('-W core/core_workload.cc -W core/core_workload.h -W core/ycsbc.cc',build)
        self.assertNotIn('/dev/nvme',build)
        self.assertNotIn('ZENFS_RRZC_NET_COST_MODE',new)

    def test_explicit_profile_not_silently_accepted_as_old(self):
        def props(name):
            return dict(s.split('=',1) for s in (HERE/name).read_text().splitlines()
                if '=' in s and not s.startswith('#'))
        old=props('workload-balanced-zns.properties');new=props('workload-locality-zns.properties')
        self.assertEqual({k for k in old.keys()|new.keys() if old.get(k)!=new.get(k)},
            {'rrzc.update_key_start','rrzc.update_key_count'})
        self.assertTrue(independent_profile(old,1200000,'50/50'))
        self.assertFalse(independent_profile(new,1200000,'50/50'))
        self.assertTrue(independent_profile(new,1200000,'50/50',(4096,61440)))
        self.assertFalse(independent_profile(old,1200000,'50/50',(4096,61440)))

    def test_actual_trace_rejects_outside_update_range(self):
        def record(op,key):
            return op+struct.pack('<I',24)+f'user{key:020}'.encode()+bytes(8)
        load=b''.join(record(b'I',k) for k in range(8))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'requests'
            path.write_bytes(load+record(b'R',1)+record(b'U',2)+record(b'U',5))
            self.assertEqual(check(path,8,2,3,update_range=(2,4))['热区更新数'],0)
            with self.assertRaises(ValueError):check(path,8,2,3)
            for k in (0,1,6,7):
                path.write_bytes(load+record(b'R',1)+record(b'U',k)+record(b'U',5))
                with self.assertRaises(ValueError):check(path,8,2,3,update_range=(2,4))


if __name__=='__main__':unittest.main()
