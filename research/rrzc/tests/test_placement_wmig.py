"""独立W迁移门控真值表、真实分配函数回归与冻结来源检查；不运行客机。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from placement_wmig_source import BASE,MODE,prepare,mode_header,adapter,digest
from test_placement_ablation import functions,environment

ROOT=Path(__file__).resolve().parents[1]

class WriteMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='rrzc-wmig-');cls.path=Path(cls.temp.name)
        cls.proof=prepare(cls.path/'独立副本');cls.fs=cls.path/'独立副本/源码/协同池副本/plugin/zenfs/fs'
        cls.flags=['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread','-I'+str(cls.fs),'-I'+str(cls.path)]
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def build(self,code,name):
        p=self.path/(name+'.cc');p.write_text(code);exe=self.path/name
        subprocess.run(self.flags+[str(p),'-o',str(exe)],check=True);return exe
    def test_modes_and_full_tag_truth_table(self):
        code=r'''#include "rrzc_placement_mode.h"
#include <cassert>
#include <iostream>
int main(){using namespace rrzc_placement;
try{const auto& c=ProcessSettings();std::cout<<Name(c.mode)<<":"<<(c.PoolK()?c.PoolK():"未设置");
for(auto m:{Mode::kLegacy,Mode::kG0,Mode::kG1,Mode::kG2,Mode::kG2WriteMigrationOff})
 for(bool append:{false,true})for(unsigned tag=0;tag<4;++tag){
 assert(SkipWriteMigration(m,append,tag)==(m==Mode::kG2WriteMigrationOff && !append && tag==2));
 assert(SkipHotMigration(m,append,tag)==(m==Mode::kG1 && !append && (tag&1)));}
setenv("ZENFS_RRZC_PLACEMENT_MODE","G0",1);assert(ProcessSettings().mode==c.mode);return 0;
}catch(const std::invalid_argument&){return 3;}}'''
        exe=self.build(code,'模式与真值表')
        for m,k in [('G0','0'),('G1','1'),('G2','1'),(MODE,'1')]:
            r=subprocess.run([str(exe)],env=environment(m),capture_output=True,text=True,check=True);self.assertEqual(r.stdout,m+':'+k)
        for bad in ['G3','',MODE+' ']:self.assertEqual(subprocess.run([str(exe)],env=environment(bad)).returncode,3)
        for key in ['POOL_K','POOL_TAGS','STREAM_CLASSIFY','POOL_FLUSH_W','POOL_PAIR_ADMISSION']:
            env=environment(MODE);env['ZENFS_RRZC_'+key]='1';self.assertEqual(subprocess.run([str(exe)],env=env).returncode,3)
    def test_real_native_allocator_old_g2_and_candidate(self):
        old=(BASE/'源码/协同池副本/plugin/zenfs/fs/rrzc_colocation_zenfs.inc').read_text()
        (self.path/'旧真实分配函数.inc').write_text(functions(old,'OldDevice'))
        (self.path/'新真实分配函数.inc').write_text(functions((self.fs/'rrzc_colocation_zenfs.inc').read_text(),'NewDevice'))
        code=(ROOT/'tests/placement_native_harness.cc').read_text()
        code=code.replace('  Env::WriteLifeTimeHint lifetime_=', '  std::atomic<uint64_t> used_capacity_{0};\n  Env::WriteLifeTimeHint lifetime_=')
        code=code.replace('sources;};','sources;uint64_t owner=0;};')
        code=code.replace('struct IOStatus {','namespace rrzc_cp {enum {kMode,kAttempt,kG1Skip,kPoolTarget};template<class... A>void Emit(int,uint64_t,uint64_t,uint64_t,std::initializer_list<uint64_t> ={}){}}\nstruct IOStatus {')
        code=code.replace('inline bool Enabled() { return true; }','inline bool Enabled() { return true; } inline uint64_t Now(){return 0;}')
        code=code.replace('  int logger_=0;','  uint64_t CompositionDevice() const {return 1;}\n  int logger_=0;')
        marker='      assert(rrzc_colocation::current.tag=='
        insert='''      if(mode==rrzc_placement::Mode::kG2WriteMigrationOff) {
        if(!new_file && rrzc_colocation::current.tag==rrzc_colocation::Tag::kShortLived)
          assert(!right && b.observed==0 && b.get_active==0 && b.put_active==0 && b.put_open==0 && b.zone.acquired==0);
        else assert(before==b.Result(right));
      }
'''
        code=code.replace(marker,insert+marker)
        exe=self.build(code,'真实函数回归')
        for mode in ['G0','G1','G2',MODE]:subprocess.run([str(exe),mode],env=environment(mode),check=True)
    def test_untouched_mapping_lifetime_and_fallback(self):
        old=BASE/'源码/协同池副本/plugin/zenfs/fs'
        for n in ['fs_zenfs.cc','io_zenfs.cc','io_zenfs.h','zbd_zenfs.cc','zbd_zenfs.h','rrzc_stream_profile.h','rrzc_colocation_pool.h','rrzc_placement_trace.h']:
            self.assertEqual((old/n).read_bytes(),(self.fs/n).read_bytes(),n)
        txt=(self.fs/'rrzc_colocation_zenfs.inc').read_text();self.assertLess(txt.index('SkipWriteMigration('),txt.index('  auto observed=ColocationObservation(true);',txt.index('IOStatus ZonedBlockDevice::TryColocation')))
        self.assertIn('(!coloc_pool_ || !coloc_pool_->Contains(z->start_))',(self.fs/'zbd_zenfs.cc').read_text())
        for n in ['rrzc_phase_time.h','rrzc_placement_mode.h','rrzc_stream_profile.h','rrzc_flush_origin.h']:
            self.assertEqual((self.fs/n).read_bytes(),(self.fs.parents[3]/n).read_bytes())
    def test_pair_uses_same_frozen_workload_and_two_explicit_modes(self):
        from run_placement_wmig_pair_host import wrapper,MODES
        from placement_business_source import wrapper as original
        old=Path('/home/data/xwz/rrzc-baselines/joint-controlled-end-to-end-v1-20261105-results/源码/run_pool_business_guest.sh').read_text()
        new=wrapper(old);expected=original(old)
        self.assertEqual(MODES,('G2',MODE))
        self.assertEqual(new.replace('( $1 == G2 || $1 == G2_WMIG_OFF )','( $1 == G0 || $1 == G1 || $1 == G2 )',1),expected)
        script=self.path/'业务入口.sh';script.write_text(new)
        subprocess.run(['bash','-n',str(script)],check=True)

    def test_aux_observer_surrounds_original_call_after_marker(self):
        text=(self.fs.parents[3]/'诊断-flush_job.cc').read_text()
        a=text.index('trace_phase("目录同步前");');b=text.index('trace_phase("目录同步后");')
        self.assertLess(a,text.index('rrzc_phase_time::Scope aux_sync',a));self.assertLess(text.index('aux_sync',a),text.index('s = output_file_directory_->FsyncWithDirOptions(',a));self.assertIn('\n      }\n',text[a:b])

if __name__=='__main__':unittest.main()
