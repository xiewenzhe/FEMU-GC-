"""交错输入守恒、原入口保留及独立部署依赖检查。"""
from collections import Counter
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from sst_interleaved_source import patched, LOOP
from sst_lifetime_source import patched as old_patch
from analyze_sst_interleaved import planned_requests, checked_requests
from analyze_sst_lifetime import planned_requests as old_requests
from run_pool_dbbench_host import VERIFIED_DB

ROOT = Path(__file__).parent


class InterleavedTest(unittest.TestCase):
    def test_request_conservation_and_reject_wrong_order(self):
        old = list(old_requests(True)); new = list(planned_requests())
        self.assertEqual(len(new), 1130496)
        self.assertEqual(Counter(new), Counter(old))
        for role in (0, 1):
            self.assertEqual([k for r,k in old if r==role], [k for r,k in new if r==role])
        self.assertNotEqual(new, old)
        rows = []; counts = [0,0]
        for seq, (role,key) in enumerate(planned_requests(True)):
            rows.append((seq,role,counts[role],key,seq*3,seq*3+1,seq*3+2)); counts[role]+=1
        self.assertEqual(len(list(checked_requests(rows,True))),736)
        with self.assertRaises(ValueError): list(checked_requests(rows[:-1],True))
        broken = list(rows); broken[2] = (*broken[2][:3],999,*broken[2][4:])
        with self.assertRaises(ValueError): list(checked_requests(broken,True))

    def test_generated_cpp_loop_matches_python_plan(self):
        # 将同一生成循环放入最小类型夹具编译，真实RocksDB编译另行执行。
        code = r'''#include "rrzc_sst_lifetime.h"
#include <vector>
#include <string>
#include <cassert>
struct Status{bool ok(){return true;}std::string ToString(){return "";}};
struct DB {Status Put(int,int,int){return {};}Status Get(int,int,std::string* out){*out=std::string(4096,'x');return {};}};
struct Stats{void FinishedOps(void*,DB*,int,int){}};
struct Thread{Stats stats;};
namespace rrzc_rotating{uint64_t Now(){static uint64_t t=0;return ++t;}}
constexpr int kWrite=1,kRead=0;const uint64_t FLAGS_num=1024;
int main(int argc,char** argv){assert(argc==2);auto plan=rrzc_sst_lifetime::Plan::Make(true,true);
rrzc_sst_lifetime::Trace trace(argv[1]);bool interleaved=true;Thread t;Thread* thread=&t;
DB db;struct {DB* db;} db_{&db};int key=0,options=0,write_options_=0;
auto GenerateKeyFromInt=[](uint64_t id,uint64_t,int* out){*out=id;};
struct Generator{int Generate(){return 0;}} generator;
std::vector<uint64_t> write_latency,read_latency;uint64_t checkpoints=0,flushes=0;
auto drain=[&](bool flush){flushes+=flush;};
auto checkpoint=[&](const char*,uint64_t,uint64_t){++checkpoints;};
'''+LOOP+r''' }
trace.Finish();assert(checkpoints==10 && write_latency.size()==480 && read_latency.size()==256);
assert(flushes==16);}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'test.cc').write_text(code)
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT),str(p/'test.cc'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test'),str(p/'trace')],check=True)
            from analyze_reproducibility import request_rows
            self.assertEqual(len(list(checked_requests(request_rows(p/'trace'),True))),736)

    def test_old_branch_and_zenfs_rules_remain(self):
        source=(VERIFIED_DB/VERIFIED_DB.name.removesuffix('-results')/'校准-db_bench_tool.cc').read_text()
        new=patched(source);old=old_patch(source)
        loop=old.split('    for(uint64_t window=0;window<plan.windows;++window) {',1)[1].split('    drain(true);trace.Finish();',1)[0]
        self.assertIn('    for(uint64_t window=0;window<plan.windows;++window) {'+loop,new)
        self.assertEqual(old.split('  void RRZCRotating(',1)[1],new.split('  void RRZCRotating(',1)[1])
        self.assertIn('Number("RRZC_SST_LIFETIME_INTERLEAVED",0)',new)
        self.assertNotIn('CompactRange(',LOOP)
        with self.assertRaises(ValueError): patched(new)

    def test_isolated_builder_import(self):
        names=('build_sst_interleaved_guest.py','build_pool_dbbench_guest.py','build_all_blocks_guest.py',
               'build_all_blocks_dbbench_guest.py','flush_wait_source.py','calibration_source.py',
               'sst_interleaved_source.py','sst_lifetime_source.py')
        with tempfile.TemporaryDirectory() as tmp:
            for n in names: shutil.copyfile(ROOT/n,Path(tmp)/n)
            subprocess.run(['python3','-I','-c','import sys;sys.path.insert(0,sys.argv[1]);import build_sst_interleaved_guest',tmp],check=True)


if __name__ == '__main__': unittest.main()
