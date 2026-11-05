"""生命周期请求计划与成功日志格式自检；不作为设备实验结果。"""
from pathlib import Path
import subprocess
import tempfile
import unittest
import json
import hashlib
from sst_lifetime_source import patched,wrapper
from run_pool_dbbench_host import VERIFIED_DB
from analyze_reproducibility import request_rows
from analyze_sst_lifetime import timing,planned_requests,checked_requests,persistent_mode
from build_sst_lifetime_guest import validate_mode


class LifetimeTest(unittest.TestCase):
    def test_native_plan_does_not_overwrite_H_and_trace_is_auditable(self):
        root=Path(__file__).parent
        code=r'''#include "rrzc_sst_lifetime.h"
#include <cassert>
int main(int argc,char** argv){
assert(argc==2);auto p=rrzc_sst_lifetime::Plan::Make(false);
assert(p.Reads()==393216 && p.Writes()==737280 && p.total==917504);
for(uint64_t w=0;w<p.windows;++w)for(uint64_t i=0;i<p.width-p.hot;++i){
auto k=p.WriteKey(w,i);assert(k>=w*p.width+p.hot && k<(w+1)*p.width);}
for(uint64_t w=0;w<p.windows;++w)for(uint64_t i=0;i<8*p.read_batch;++i){
auto k=p.ReadKey(w,i);assert(k>=w*p.width && k<w*p.width+p.hot);}
auto fixed=rrzc_sst_lifetime::Plan::Make(false,true);
assert(fixed.Reads()==p.Reads() && fixed.Writes()==p.Writes());
for(uint64_t w=0;w<p.windows;++w)for(uint64_t i=0;i<8*p.read_batch;++i){
assert(fixed.ReadKey(w,i)==i%p.hot);}
for(uint64_t w=0;w<p.windows;++w)for(uint64_t i=0;i<p.width-p.hot;++i){
assert(fixed.WriteKey(w,i)==p.WriteKey(w,i));}
rrzc_sst_lifetime::Trace t(argv[1]);t.Record(true,4096,1,2,3);t.Record(false,0,4,5,6);t.Finish();
}'''
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);source=folder/'test.cc';source.write_text(code);binary=folder/'test';trace=folder/'trace'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(root),str(source),'-o',str(binary)],check=True)
            subprocess.run([str(binary),str(trace)],check=True)
            self.assertEqual([r[:4] for r in request_rows(trace)],[(0,1,0,4096),(1,0,0,0)])

    def test_only_new_method_is_added_and_wrapper_keeps_rules(self):
        native=VERIFIED_DB/VERIFIED_DB.name.removesuffix('-results')/'校准-db_bench_tool.cc'
        old=native.read_text();new=patched(old)
        self.assertEqual(old.split('  void RRZCRotating(ThreadState* thread) {',1)[1],
                         new.split('  void RRZCRotating(ThreadState* thread) {',1)[1])
        self.assertIn('method = &Benchmark::RRZCSstLifetime;',new)
        text=wrapper((VERIFIED_DB/'源码/run_pool_business_guest.sh').read_text())
        subprocess.run(['bash','-n'],input=text,text=True,check=True)
        for token in ['ZENFS_RRZC_CANDIDATE_POOL=original','ZENFS_RR_STRICT=1','ZENFS_RR_ENABLE=1','ZENFS_RRZC_POOL_K=1']:
            self.assertIn(token,text)
        self.assertIn('--reads=393216 --writes=737280',text)
        self.assertLess(text.index('calibration_guest.py" gate'),text.rindex('strict_stats_guest.py" > "$result/strict.before.json"'))
        self.assertIn('strict.preparation.before.json',text)
        fixed=wrapper((VERIFIED_DB/'源码/run_pool_business_guest.sh').read_text(),True)
        self.assertEqual(fixed.replace('RRZC_SST_LIFETIME_PERSISTENT_H=1 ',''),text)
        subprocess.run(['bash','-n'],input=fixed,text=True,check=True)
        with self.assertRaises(ValueError):patched(new)

    def test_logical_plan_rejects_wrong_key_and_distinguishes_H_mode(self):
        self.assertFalse(persistent_mode('旧轮归档'))
        self.assertTrue(persistent_mode('SST生命周期计划：固定H=1\n'))
        with self.assertRaises(ValueError):persistent_mode('SST生命周期计划：固定H=1\n'*2)
        with self.assertRaises(ValueError):list(checked_requests([(0,1,0,999,1,2,3)],True))
        with self.assertRaises(ValueError):list(checked_requests([(0,1,0,4096,1,2,3)],True))
        differences=0
        for old,fixed in zip(planned_requests(False),planned_requests(True)):
            if old[0]:self.assertEqual(old,fixed)
            else:
                self.assertLess(fixed[1],4096)
                differences+=old!=fixed
        self.assertEqual(differences,5*65536)

    def test_real_completed_lifetime_timing_and_wrong_stage_rejected(self):
        root=VERIFIED_DB.parent/'joint-controlled-sst-lifetime-v1-20261029-results/逐臂采证/p1'
        if not root.exists():self.skipTest('真实生命周期归档尚未产生')
        result=timing(root)
        self.assertGreater(result['请求首尾秒'],0)
        self.assertAlmostEqual(result['请求窗口吞吐ops']*result['请求首尾秒'],1130496)
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder);text=(root/'run.log').read_text()
            text=text.replace('SST生命周期阶段：序号=60，','SST生命周期阶段：序号=61，',1)
            (target/'run.log').write_text(text)
            with self.assertRaises(ValueError):timing(target)

    def test_reuse_mode_accepts_same_workload_and_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'校准-db_bench_tool.cc').write_text('固定客户端');(p/'rrzc_sst_lifetime.h').write_text('固定计划')
            sha=lambda name:hashlib.sha256((p/name).read_bytes()).hexdigest()
            record=dict(生命周期客户端源码SHA256=sha('校准-db_bench_tool.cc'),生命周期计划头SHA256=sha('rrzc_sst_lifetime.h'))
            (p/'复用真实DB程序.json').write_text(json.dumps(record));validate_mode(p)
            (p/'rrzc_sst_lifetime.h').write_text('另一计划')
            with self.assertRaises(AssertionError):validate_mode(p)


if __name__=='__main__':unittest.main()
