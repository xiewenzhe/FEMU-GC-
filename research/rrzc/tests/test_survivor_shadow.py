"""影子观察真实分配入口回归；明确观测失败不能阻断已有W回退。"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from placement_survivor_shadow_source import BASE,MODE,prepare
from test_placement_ablation import environment,functions

ROOT=Path(__file__).resolve().parents[1]


class ShadowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='rrzc-wshadow-');cls.path=Path(cls.tmp.name)
        prepare(cls.path/'副本');cls.fs=cls.path/'副本/源码/协同池副本/plugin/zenfs/fs'

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_real_g2_regression_and_shadow_no_commit(self):
        old=BASE/'源码/协同池副本/plugin/zenfs/fs'
        (self.path/'旧真实分配函数.inc').write_text(functions((old/'rrzc_colocation_zenfs.inc').read_text(),'OldDevice'))
        (self.path/'新真实分配函数.inc').write_text(functions((self.fs/'rrzc_colocation_zenfs.inc').read_text(),'NewDevice'))
        text=(ROOT/'tests/placement_native_harness.cc').read_text()
        text='#include "rrzc_write_survivor.h"\n'+text
        text=text.replace('  Env::WriteLifeTimeHint lifetime_=', '  std::atomic<uint64_t> used_capacity_{0};\n  Env::WriteLifeTimeHint lifetime_=')
        text=text.replace('sources;};','sources;uint64_t owner=0;rrzc_write_survivor::State write_history=rrzc_write_survivor::State::kNew;};')
        text=text.replace('struct IOStatus {','namespace rrzc_cp {enum {kMode,kAttempt,kG1Skip,kPoolTarget};template<class... A>void Emit(int,uint64_t,uint64_t,uint64_t,std::initializer_list<uint64_t> ={}){}}\nstruct IOStatus {')
        text=text.replace('inline bool Enabled() { return true; }','inline bool Enabled() { return true; } inline uint64_t Now(){return 0;}')
        text=text.replace('  int logger_=0;','  uint64_t CompositionDevice() const {return 1;}\n  int logger_=0;')
        text=text.replace('observation;\n  Zone zone;','observation;\n  bool observation_failure=false;\n  Zone zone;')
        text=text.replace('++observed;return observation;','++observed;if(observation_failure)throw std::runtime_error("合成查询失败");return observation;')
        text=text.replace('  switch(scenario) {','  current.write_history=static_cast<rrzc_write_survivor::State>(std::atoi(std::getenv("测试历史")));\n  switch(scenario) {')
        text=text.replace('      assert(rrzc_colocation::current.tag==', '''      if(mode==rrzc_placement::Mode::kG2WriteSurvivor) {
        const bool gate=!new_file && rrzc_colocation::current.tag==rrzc_colocation::Tag::kShortLived &&
          rrzc_colocation::current.write_history==rrzc_write_survivor::State::kCommitted;
        if(gate){assert(!left && !right && a.observed==0 && b.observed==1);
          assert(b.get_active==0 && b.put_active==0 && b.put_open==0 && b.zone.acquired==0);
          assert(a.coloc_pool_->Snapshot().size()==b.coloc_pool_->Snapshot().size());}
        else assert(before==b.Result(right));
      }
      assert(rrzc_colocation::current.tag==''')
        text=text.replace('  std::cout<<"模式="', '''  if(mode==rrzc_placement::Mode::kG2WriteSurvivor) {
    NewDevice d;d.InitColocationPool();Prepare(d,0);d.observation_failure=true;
    rrzc_colocation::current.write_history=rrzc_write_survivor::State::kCommitted;
    Zone* target=nullptr;d.TryColocation(1,Env::kNormal,false,&target);
    assert(!target && d.get_active==0 && d.zone.acquired==0);
  }
  std::cout<<"模式="''')
        source=self.path/'真实影子回归.cc';source.write_text(text);exe=self.path/'真实影子回归'
        subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread',
                        '-I'+str(self.fs),str(source),'-o',str(exe)],check=True)
        for mode in ['G0','G1','G2',MODE]:
            for state in range(3):
                env=environment(mode);env['测试历史']=str(state)
                subprocess.run([str(exe),mode],env=env,check=True,capture_output=True)

    def test_all_other_sources_and_algorithm_unchanged(self):
        old=BASE/'源码/协同池副本/plugin/zenfs/fs'
        for path in old.iterdir():
            if path.is_file() and path.name not in ['fs_zenfs.cc','rrzc_colocation_zenfs.inc']:
                self.assertEqual(path.read_bytes(),(self.fs/path.name).read_bytes(),path.name)
        old_fs=(old/'fs_zenfs.cc').read_text();new_fs=(self.fs/'fs_zenfs.cc').read_text()
        new_fs=new_fs.replace('    if(rrzc_write_survivor::Enabled(rrzc_placement::ProcessSettings().mode) ||\n'
            '       rrzc_placement::ProcessSettings().mode==rrzc_placement::Mode::kG2) {',
            '    if(rrzc_write_survivor::Enabled(rrzc_placement::ProcessSettings().mode)) {')
        self.assertEqual(old_fs,new_fs)
        self.assertIn('catch(const std::exception& error)',(self.fs/'rrzc_colocation_zenfs.inc').read_text())


if __name__=='__main__':unittest.main()
