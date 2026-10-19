"""真实编译原版和派生CoreWorkload；请求测试不冒充设备结果。"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import struct
import sys

HERE=Path(__file__).resolve().parent
UPSTREAM=HERE.parents[2]/'build-femu/rrzc/ycsb-upstream-20261017'
sys.path.insert(0,str(HERE))
from check_ycsb_independent_trace import check


class IndependentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='rrzc-independent-')
        cls.root=Path(cls.temp.name)
        cls.work=cls.root/'core';cls.work.mkdir()
        for name in ('core_workload.h','core_workload.cc'):
            shutil.copyfile(UPSTREAM/'core'/name,cls.work/name)
        subprocess.run(['patch','-p1','-i',str(HERE/'ycsb-independent-keys.patch')],
            cwd=cls.root,check=True,capture_output=True)
        for mode,core in (('before',UPSTREAM/'core'),('after',cls.work)):
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(core),'-I'+str(UPSTREAM/'core'),'-I'+str(UPSTREAM),
                str(HERE/'ycsb_key_trace.cc'),str(core/'core_workload.cc'),
                str(UPSTREAM/'core/acknowledged_counter_generator.cc'),
                '-o',str(cls.root/mode)],check=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def trace(self,mode='after',*args):
        return subprocess.run([str(self.root/mode),*args],capture_output=True,
            env={**os.environ,'YCSB_RANDOM_SEED':'20261017'})

    def test_default_and_explicit_disabled_byte_identical_to_original(self):
        for dist in ('uniform','latest','zipfian'):
            args=('requestdistribution='+dist,)
            before=self.trace('before',*args)
            self.assertEqual(before.returncode,0)
            for switch in ((),('rrzc.independent_keys=false',)):
                result=self.trace('after',*args,*switch)
                self.assertEqual(result.returncode,0)
                self.assertEqual(before.stdout,result.stdout)

    def test_enabled_repeats_ranges_and_update_overlap(self):
        first=self.trace('after','rrzc.independent_keys=true')
        self.assertEqual(first.returncode,0,first.stderr)
        self.assertEqual(first.stdout,self.trace('after','rrzc.independent_keys=true').stdout)
        reads=[];updates=[]
        for line in first.stdout.decode().splitlines():
            op,key,*_=line.split();number=int(key.removeprefix('user'))
            (reads if op=='R' else updates).append(number)
        self.assertEqual(len(reads)+len(updates),20000)
        self.assertTrue(reads and updates)
        self.assertLess(max(reads),64)
        self.assertLess(max(updates),1024)
        self.assertTrue(any(k<64 for k in updates) and any(k>=64 for k in updates))
        self.assertGreater(len(set(updates)),900)

    def test_invalid_configs_fail_without_requests(self):
        for arg in ('rrzc.read_key_count=0','rrzc.read_key_count=1','rrzc.read_key_count=1025',
            'rrzc.read_key_count=-1','rrzc.read_key_count=64x','rrzc.read_key_count=',
            'rrzc.read_key_count=18446744073709551616','threadcount=2','insertstart=1',
            'readproportion=nan','updateproportion=-0.2','readproportion=0.9',
            'scanproportion=0.1','insertproportion=0.1','readmodifywriteproportion=0.1',
            'requestdistribution=latest','zipfian_const=0.99'):
            with self.subTest(arg=arg):
                result=self.trace('after','rrzc.independent_keys=true',arg)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(result.stdout,b'')
        self.assertNotEqual(self.trace('after','rrzc.independent_keys=1').returncode,0)

    def test_balanced_requests_repeat_and_keep_key_ranges(self):
        args=('rrzc.independent_keys=true','readproportion=0.5','updateproportion=0.5')
        first=self.trace('after',*args)
        self.assertEqual(first.returncode,0,first.stderr)
        self.assertEqual(first.stdout,self.trace('after',*args).stdout)
        reads=[];updates=[]
        for line in first.stdout.decode().splitlines():
            op,key,*_=line.split();number=int(key.removeprefix('user'))
            (reads if op=='R' else updates).append(number)
        self.assertEqual(len(reads)+len(updates),20000)
        self.assertTrue(9500<=len(reads)<=10500)
        self.assertLess(max(reads),64)
        self.assertLess(max(updates),1024)
        self.assertTrue(any(k<64 for k in updates) and any(k>=64 for k in updates))


class TraceAuditTest(unittest.TestCase):
    def test_complete_trace_and_corrupt_boundaries(self):
        def record(op,key,value=0):
            raw=f'user{key:020}'.encode()
            return op+struct.pack('<I',len(raw))+raw+struct.pack('<Q',value)
        load=b''.join(record(b'I',k) for k in range(4))
        good=load+record(b'R',1)+record(b'U',0)+record(b'U',3)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'requests';path.write_bytes(good)
            self.assertEqual(check(path,4,2,3)['热区更新数'],1)
            for bad in (good[:-1],load+record(b'R',2)+record(b'U',0)+record(b'U',3),
                load+record(b'R',1,1)+record(b'U',0)+record(b'U',3),
                load+record(b'R',1)+record(b'U',4)+record(b'U',3),
                load+record(b'R',1)+record(b'U',3)+record(b'U',3),
                record(b'I',1)+good,good+record(b'R',1)):
                with self.subTest(length=len(bad)):
                    path.write_bytes(bad)
                    with self.assertRaises(ValueError):check(path,4,2,3)

    def test_registered_profile_preserves_baselines_and_recompiles_class_consumers(self):
        def props(name):
            return dict(s.split('=',1) for s in (HERE/name).read_text().splitlines()
                if '=' in s and not s.startswith('#'))
        old=props('workload-read-backpressure-zns.properties');new=props('workload-independent-zns.properties')
        self.assertEqual({k for k in old.keys()|new.keys() if old.get(k)!=new.get(k)},
            {'operationcount','readproportion','updateproportion','requestdistribution',
                'rrzc.independent_keys','rrzc.read_key_count'})
        text=(HERE/'build_ycsb_independent_guest.sh').read_text()
        self.assertIn('-W core/core_workload.cc -W core/core_workload.h -W core/ycsbc.cc',text)
        self.assertNotIn('sudo',text)
        run=(HERE/'run_ycsb_independent_guest.sh').read_text()
        previous=(HERE/'run_ycsb_read_pilot_guest.sh').read_text()
        self.assertEqual([s for s in run.splitlines() if 'ZENFS_' in s],
            [s for s in previous.splitlines() if 'ZENFS_' in s])


if __name__=='__main__':unittest.main()
