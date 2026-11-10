"""核验完整三场景请求计划、真实队列/消费者一致及放置源码不变。"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from lifetime_scenarios import SCENARIOS,identity,slot
from placement_lifetime_scenarios_source import prepare
from placement_origin_source import BASE


class ScenarioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='rrzc-lifetime-scenarios-')
        cls.path=Path(cls.temp.name);prepare(cls.path/'独立副本')
        cls.source=cls.path/'独立副本/源码'
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_actual_plan_and_queue_full_streams_match_independent_plan(self):
        text=r'''#include "rrzc_end_to_end.h"
#include <cassert>
#include <cstdio>
int main(){try{auto p=rrzc_sst_lifetime::Plan::Make(false,true);
assert(p.Writes()==1474560 && p.Reads()==786432 && p.total==1048576);
rrzc_end_to_end::Queue q(p,4096);uint64_t seq=0;
auto emit=[&](bool w,uint64_t key){assert(key<p.total);auto r=q.Take(seq++,w,key);
 unsigned char bytes[9];bytes[0]=r.write;for(unsigned i=0;i<8;++i)bytes[i+1]=r.key>>(i*8);
 assert(std::fwrite(bytes,1,9,stdout)==9);};
for(uint64_t w=0;w<p.windows;++w)for(uint64_t pass=0;pass<2;++pass){uint64_t reads=0;
 for(uint64_t n=0;n<p.width-p.hot;++n){emit(true,p.WriteKey(w,n));
  while(reads<(n+1)*32768/61440)emit(false,p.ReadKey(w,pass*32768+reads++));}}
assert(seq==2260992);return 0;}catch(const std::exception&){return 3;}}
'''
        file=self.path/'真实客户端队列.cc';file.write_text(text);binary=self.path/'真实客户端队列'
        subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread',
            '-I'+str(self.source),str(file),'-o',str(binary)],check=True)
        hashes=[]
        for scenario in SCENARIOS:
            env=dict(os.environ,RRZC_LIFETIME_SCENARIO=scenario)
            output=self.path/(scenario+'.bin')
            with output.open('wb') as stream:subprocess.run([str(binary)],env=env,stdout=stream,check=True)
            actual=hashlib.sha256(output.read_bytes()).hexdigest()
            self.assertEqual(actual,identity(scenario)['操作Key流SHA256']);hashes.append(actual)
        self.assertEqual(len(set(hashes)),3)
        for bad in ('','original','REPEAT','repeat '):
            result=subprocess.run([str(binary)],env=dict(os.environ,RRZC_LIFETIME_SCENARIO=bad),
                stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            self.assertNotEqual(result.returncode,0)

    def test_only_workload_header_changes_after_origin_separation(self):
        fs=self.source/'协同池副本/plugin/zenfs/fs'
        old=BASE/'源码/协同池副本/plugin/zenfs/fs'
        for path in old.iterdir():
            if path.is_file() and path.name not in ('io_zenfs.h','rrzc_colocation_pool.h'):
                self.assertEqual(path.read_bytes(),(fs/path.name).read_bytes(),path.name)
        self.assertEqual((self.source/'rrzc_end_to_end.h').read_bytes(),(BASE/'源码/rrzc_end_to_end.h').read_bytes())
        self.assertEqual((self.source/'校准-db_bench_tool.cc').read_bytes(),(BASE/'源码/校准-db_bench_tool.cc').read_bytes())
        self.assertEqual([slot('repeat',w) for w in range(12)],[0]*12)
        self.assertEqual([slot('spread',w) for w in range(12)],list(range(12)))
        self.assertEqual([slot('switch',w) for w in range(12)],[0]*6+list(range(1,7)))


if __name__=='__main__':unittest.main()
