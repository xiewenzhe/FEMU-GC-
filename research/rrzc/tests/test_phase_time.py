"""核验跨编译单元计时、并发缓冲以及窗口交叠分账，示例不作为实验数据。"""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import phase_time_source
from analyze_phase_time import analyze,merge

ROOT=Path(__file__).resolve().parents[1]


class PhaseTimeTest(unittest.TestCase):
    def test_frozen_client_header_closure(self):
        from run_phase_time_host import PRIOR,freeze_client_headers
        with tempfile.TemporaryDirectory() as tmp:
            result=freeze_client_headers(PRIOR/PRIOR.name.removesuffix('-results'),Path(tmp))
            self.assertIn('rrzc_tls_seed.h',result)
            self.assertIn('rrzc_io_trace.h',result)
            self.assertEqual(len(result),8)

    def test_guest_builder_imports_with_only_deployed_modules(self):
        from run_phase_time_host import GUEST_MODULES
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            for n in GUEST_MODULES:
                shutil.copyfile(ROOT/'tests'/n,p/n)
            subprocess.run(['python3','-I','-c',
                'import sys;sys.path.insert(0,sys.argv[1]);import build_phase_time_guest',str(p)],check=True)

    def test_buffer_shared_across_units_and_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'a.cc').write_text('#include "rrzc_phase_time.h"\nvoid other(){rrzc_phase_time::Scope s(8,123);}\n')
            (p/'b.cc').write_text('#include "rrzc_phase_time.h"\n#include <thread>\n'
                'void other();int main(){rrzc_phase_time::Scope s(1);std::thread t(other);t.join();}\n')
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'model'),str(p/'a.cc'),str(p/'b.cc'),'-o',str(p/'test')],check=True)
            env=dict(os.environ);env.pop('RRZC_PHASE_TIME',None)
            subprocess.run([str(p/'test')],env=env,check=True)
            self.assertFalse((p/'trace').exists())
            env['RRZC_PHASE_TIME']=str(p/'trace')
            subprocess.run([str(p/'test')],env=env,check=True)
            raw=(p/'trace').read_bytes();self.assertEqual(raw[:16],b'RRZCTM01'+bytes(8))
            rows=list(struct.iter_unpack('<6Q',raw[16:]));self.assertEqual({r[0] for r in rows},{1,8})
            self.assertEqual(len({r[3] for r in rows}),2)
            self.assertTrue(all(r[1]<=r[2] for r in rows))

    def test_synthetic_nested_intervals_close_without_double_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            rows=[(0,0,0,1,100,110,110),(1,1,0,2,200,210,210),(2,0,1,3,300,310,310)]
            (p/'请求提交.bin').write_bytes(b'RRZCREQ1'+b''.join(struct.pack('<7Q',*r) for r in rows))
            scopes=[(1,120,140),(2,140,150),(3,220,240),(4,240,260),
                    (5,121,139),(6,125,135),(8,126,130),(10,124,136),(11,101,109),(13,102,108)]
            data=lambda values:b'RRZCTM01'+bytes(8)+b''.join(struct.pack('<6Q',k,a,b,1,0,(1<<64)-1) for k,a,b in values)
            (p/'等待区间.bin').write_bytes(data(scopes))
            r=analyze(p);part=r['窗口分账']
            self.assertAlmostEqual(part['请求窗口秒'],210/1e9)
            self.assertAlmostEqual(part['API累计秒'],30/1e9)
            self.assertAlmostEqual(part['显式等待并集秒'],70/1e9)
            self.assertAlmostEqual(part['其他请求间隔秒'],110/1e9)
            strict=next(x for x in r['内部区间'] if x['类别']=='读请求严格RR服务')
            self.assertAlmostEqual(strict['与API重叠秒'],8/1e9)
            (p/'等待区间.bin').write_bytes(data(scopes+[(1,145,160)]))
            with self.assertRaises(ValueError):analyze(p)
        self.assertEqual(merge([(1,3),(2,4),(4,5)]),[(1,5)])

    def test_real_frozen_source_accepts_only_observation_changes(self):
        from run_integrated_greedy_host import PRIOR
        root=PRIOR/PRIOR.name.removesuffix('-results')
        fs=root/'协同池副本/plugin/zenfs/fs'
        for name,fn in (('zbd_zenfs.cc',phase_time_source.zbd),('fs_zenfs.cc',phase_time_source.fs),
                        ('io_zenfs.cc',phase_time_source.io)):
            old=(fs/name).read_text();new=fn(old)
            self.assertIn(old.splitlines()[0],new)
            with self.assertRaises(ValueError):fn(new)
        old=(root/'校准-db_bench_tool.cc').read_text();new=phase_time_source.client(old)
        for marker in ('auto status=db_.db->Get(options,key,&value);',
                       'auto status=db_.db->Put(write_options_,key,value);',
                       'trace.Record(true,id,before,after,rrzc_rotating::Now());',
                       'trace.Record(false,id,before,after,rrzc_rotating::Now());'):
            self.assertEqual(old.count(marker),new.count(marker))


if __name__=='__main__':unittest.main()
