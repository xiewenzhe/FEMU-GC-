"""第二阶段本地编译及生命周期故障注入；合成夹具不作为设备实验结果。"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from analyze_placement_trace import analyze,rows
from build_placement_trace import prepare,SOURCE,ROOT
from test_placement_ablation import functions
import placement_trace_source as transform


class TraceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='rrzc-placement-ledger-');cls.path=Path(cls.tmp.name)
        cls.copy=cls.path/'独立源';cls.identity=prepare(SOURCE,cls.copy)
        cls.flags=['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread',
            '-I'+str(cls.copy/'新版源码'),'-I'+str(cls.path)]
        cls.env={k:v for k,v in os.environ.items() if not k.startswith('ZENFS_RRZC_')}
        cls.env['ZENFS_RRZC_PLACEMENT_MODE']='G2'

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def run_cpp(self,code,name):
        source=self.path/(name+'.cc');source.write_text(code);binary=self.path/name
        subprocess.run(self.flags+[str(source),'-o',str(binary)],check=True,timeout=30)
        trace=self.path/(name+'.bin');env=dict(self.env,ZENFS_RRZC_PLACEMENT_TRACE=str(trace))
        subprocess.run([str(binary)],env=env,check=True,timeout=30)
        return trace

    def test_actual_observer_methods_and_lifecycle_replay(self):
        (self.path/'真实观测方法.inc').write_text(transform.IO_IMPL)
        trace=self.run_cpp((ROOT/'tests/placement_trace_harness.cc').read_text(),'生命周期')
        report=analyze(trace)
        self.assertTrue(report['事件账目闭合']);self.assertEqual(report['原生映射核验次数'],1)
        self.assertEqual(report['有效复制字节'],1536)
        targets=report['目标'];self.assertEqual(len(targets),5)
        self.assertTrue(all(x['引用数值一致'] for x in targets))
        self.assertEqual(targets[0]['当前组成']['W_only'],1024)
        self.assertEqual(targets[1]['当前组成']['HW'],1024)
        self.assertEqual(targets[1]['当前组成']['SST有效总量'],1024)
        self.assertEqual(targets[2]['当前组成']['待提交迁移引用'],1024)
        self.assertEqual(targets[3]['当前组成']['W_only'],1024)
        self.assertEqual(targets[3]['当前组成']['历史曾H_SST字节'],1024)
        self.assertEqual(targets[4]['当前组成']['退役保留引用'],1024)
        at_end=report['业务边界'][0]['各Zone']
        one=next(z for z in at_end if z['Zone']==4096);three=next(z for z in at_end if z['Zone']==12288)
        self.assertEqual(one['代际'],1);self.assertEqual(one['临时恢复引用'],512)
        self.assertEqual(three['非SST字节'],16);self.assertEqual(three['类别未确认字节'],32)
        self.assertEqual(three['SST有效总量'],0)

    def test_hot_projection_matches_original_algorithm_and_silent_expiry(self):
        old=(SOURCE/'新版源码/rrzc_stream_profile.h').read_text()
        (self.path/'原热度.h').write_text(old.replace('namespace rrzc_stream {','namespace old_stream {'))
        code=r'''#include "原热度.h"
#include "rrzc_stream_profile.h"
#include <cassert>
int main(){old_stream::Profile old;rrzc_stream::Profile now;
for(unsigned i=0;i<150000;++i){
 if(i%11==0){old_stream::Tick();rrzc_stream::Tick();}
 else{uint64_t offset=(i<256?i:(i/5000)%2?i%19:0)*4096;
 auto a=old.Read(offset,4096,65536);auto b=now.Read(offset,4096,65536);
 assert(a.hot==b.hot && a.changed==b.changed && a.reads==b.reads && a.reused==b.reused && a.bytes==b.bytes);}
 if(i%127==0){const uint64_t size=(i/7000)%2?65536:UINT64_C(67108864);
 now.ObserveSnapshot(size);auto a=old.Snapshot(size);auto b=now.Snapshot(size);assert(a.hot==b.hot);
 rrzc_placement_trace::Emit(rrzc_placement_trace::kClock,0,now.ObservationId(),rrzc_stream::Now(),{a.hot,size});}
}
for(unsigned i=0;i<65536;++i){old_stream::Tick();rrzc_stream::Tick();}
auto a=old.Snapshot(65536);auto b=now.Snapshot(65536);assert(a.hot==b.hot && !a.hot);
rrzc_placement_trace::Emit(rrzc_placement_trace::kClock,0,now.ObservationId(),rrzc_stream::Now(),{a.hot,65536});
}'''
        trace=self.run_cpp(code,'热度投影')
        report=analyze(trace)
        self.assertGreater(report['热度即时对照次数'],1000)
        self.assertLess(report['各类事件'].get('热度',0),15000,'禁止退化成逐IO记录')

    def test_disabled_observation_has_no_file_and_no_profile_event(self):
        code=r'''#include "rrzc_stream_profile.h"
#include <cassert>
int main(){assert(!rrzc_placement_trace::Enabled());rrzc_stream::Profile p;
for(unsigned i=0;i<300;++i)p.Read(0,4096,65536);
assert(p.ObservationId()==0 && p.Snapshot(65536).hot);}
'''
        source=self.path/'关闭.cc';source.write_text(code);binary=self.path/'关闭'
        subprocess.run(self.flags+[str(source),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],env=self.env,check=True)

    def test_observation_failure_never_changes_original_hotness(self):
        code=r'''#include "rrzc_stream_profile.h"
#include <cassert>
int main(){rrzc_stream::Profile profile;
for(unsigned i=0;i<200;++i)profile.Read(0,4096,65536);
rrzc_placement_trace::Invalidate();
for(unsigned i=0;i<200;++i)profile.Read(0,4096,65536);
assert(profile.Snapshot(65536).hot);}
'''
        trace=self.run_cpp(code,'观测失效')
        with self.assertRaisesRegex(ValueError,'未完成写入'):analyze(trace)

    def test_source_preserves_policy_and_native_failure_paths(self):
        old=SOURCE/'新版源码';new=self.copy/'新版源码'
        for n in ['rrzc_colocation_pool.h','rrzc_placement_mode.h','rrzc_unit_cost_selector.h',
                  'rrzc_first_action.h','rrzc_single_recovery.h']:
            if (old/n).exists():self.assertEqual((old/n).read_bytes(),(new/n).read_bytes(),n)
        self.assertIn('rrzc_cp::kUncertain',(new/'zbd_zenfs.cc').read_text())
        self.assertIn('rrzc_cp::kUncertain',(new/'fs_zenfs.cc').read_text())
        self.assertIn('if (!file.TryAcquireWRLock())',(new/'fs_zenfs.cc').read_text())
        self.assertEqual((old/'fs_zenfs.cc').read_text().count('if (!file.TryAcquireWRLock())'),
                         (new/'fs_zenfs.cc').read_text().count('if (!file.TryAcquireWRLock())'))
        self.assertFalse(self.identity['账目参与策略'])

    def test_observed_real_target_functions_preserve_old_g2_and_mode_routes(self):
        old=(SOURCE/'旧G2源码/rrzc_colocation_zenfs.inc').read_text()
        new=(self.copy/'新版源码/rrzc_colocation_zenfs.inc').read_text()
        (self.path/'旧真实分配函数.inc').write_text(functions(old,'OldDevice'))
        (self.path/'新真实分配函数.inc').write_text(functions(new,'NewDevice'))
        source=(ROOT/'tests/placement_native_harness.cc').read_text()
        source='#include "rrzc_placement_trace.h"\nnamespace rrzc_cp=rrzc_placement_trace;\n'+source
        source=source.replace('inline bool Enabled() { return true; }',
            'inline bool Enabled() { return true; } inline uint64_t Now(){return 0;}')
        source=source.replace('std::set<uint64_t> sources;','std::set<uint64_t> sources;uint64_t owner=0;')
        source=source.replace('uint64_t start_=7,capacity_=128,generation=1;',
            'uint64_t start_=7,capacity_=128,generation=1;std::atomic<uint64_t> used_capacity_{0};')
        source=source.replace('int logger_=0;', 'int logger_=0;uint64_t CompositionDevice(){return 1;}')
        src=self.path/'观测分配.cc';src.write_text(source);binary=self.path/'观测分配'
        subprocess.run(self.flags+[str(src),'-o',str(binary)],check=True,timeout=30)
        for mode in ['G0','G1','G2']:
            plain=dict(self.env,ZENFS_RRZC_PLACEMENT_MODE=mode)
            a=subprocess.run([str(binary),mode],env=plain,check=True,capture_output=True)
            path=self.path/(mode+'目标.bin')
            b=subprocess.run([str(binary),mode],env=dict(plain,ZENFS_RRZC_PLACEMENT_TRACE=str(path)),
                check=True,capture_output=True)
            self.assertEqual(a.stdout,b.stdout)
            records=list(rows(path));self.assertEqual(sum(r[3]==9 for r in records),34)
            skipped=sum(r[3]==10 for r in records)
            self.assertEqual(skipped,4 if mode=='G1' else 0)

    def synthetic(self,name,items,complete=1,dropped=0):
        path=self.path/(name+'.bin')
        with path.open('wb') as f:
            f.write(b'RRZCCP01'+struct.pack('<3Q',len(items),dropped,complete))
            for seq,(kind,device,owner,clock,args) in enumerate(items,1):
                row=[seq,seq,1,kind,device,owner,clock,*args];row+=[0]*(16-len(row))
                f.write(struct.pack('<16Q',*row))
        return path

    def base(self):return [(1,1,0,0,[4096,0,4096,1]),(20,0,3,0,[]),
        (2,1,2,0,[101,3,1,0,0,0,1024]),(3,1,2,0,[4096,0,4096,1024,1])]

    def test_rejects_reset_with_stale_extent_and_uncertain_sync(self):
        with self.assertRaisesRegex(ValueError,'Reset前仍有引用'):
            analyze(self.synthetic('残留Reset',self.base()+[(15,1,0,0,[4096,0,1,1])]))
        with self.assertRaisesRegex(ValueError,'原生同步失败'):
            analyze(self.synthetic('同步失败',self.base()+[(18,1,2,0,[9])]))

    def test_rejects_corrupt_snapshot_duplicate_extent_and_truncated_trace(self):
        with self.assertRaisesRegex(ValueError,'原生extent快照'):
            analyze(self.synthetic('错快照',self.base()+[(23,1,2,0,[]),(24,1,2,0,[4096,0,4096,512]),(25,1,2,0,[])]))
        with self.assertRaisesRegex(ValueError,'重复增加'):
            analyze(self.synthetic('重复extent',self.base()+[self.base()[-1]]))
        for closed,dropped in [(0,0),(1,1)]:
            with self.assertRaises(ValueError):analyze(self.synthetic(f'不完整{closed}',self.base(),closed,dropped))

    def test_marks_cross_thread_heat_publication_ambiguous(self):
        items=self.base()+[(12,1,2,256,[4096,0,3072,1024,0,1,1]),
            (8,0,3,256,[1,65536,256,255,1048576,1024])]
        report=analyze(self.synthetic('并发未发布',items))
        self.assertFalse(report['目标'][0]['H状态已确认'])
        self.assertEqual(report['目标'][0]['当前组成']['H状态未确认'],1024)

    def test_metadata_occupancy_stays_outside_sst_and_resets(self):
        items=self.base()+[(1,1,0,0,[8192,0,4096,1]),(28,1,0,0,[8192,0,1,9216]),
            (29,1,0,0,[8192,0,10240,1,256]),(27,1,0,0,[1,0]),
            (15,1,0,0,[8192,0,1,1]),(27,1,0,0,[2,0])]
        result=analyze(self.synthetic('元数据占用',items))
        first=result['业务边界'][0]['各Zone'][1];last=result['业务边界'][1]['各Zone'][1]
        self.assertEqual(first['元数据区已写占用_非有效映射'],2048)
        self.assertEqual(first['SST有效总量'],0)
        self.assertEqual(last['元数据区已写占用_非有效映射'],0)

    def test_all_four_disjoint_sst_categories_and_extent_bounds(self):
        items=[(1,1,0,0,[4096,0,4096,1])]
        for i,(hot,w) in enumerate([(0,0),(0,1),(1,0),(1,1)]):
            profile=10+i;owner=20+i
            items += [(20,0,profile,0,[]),(2,1,owner,0,[i,profile,1,w,0,0,512])]
            if hot:items += [(8,0,profile,256,[1,65536,256,255,1048576,512])]
            items += [(3,1,owner,256,[4096,0,4096+i*512,512,1])]
        items += [(27,1,0,256,[1,0])]
        result=analyze(self.synthetic('四桶互斥',items))
        zone=result['业务边界'][0]['各Zone'][0]
        self.assertEqual([zone[n] for n in ['H_only','W_only','HW','未分类SST']],[512]*4)
        self.assertEqual(zone['SST有效总量'],2048)
        with self.assertRaisesRegex(ValueError,'越过Zone容量'):
            analyze(self.synthetic('越界extent',self.base()+[(3,1,2,0,[4096,0,8191,2,1])]))


if __name__=='__main__':unittest.main()
