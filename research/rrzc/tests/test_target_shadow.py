"""本地编译真实分配函数与观察函数；桩后端不代表设备或业务收益实验。"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from placement_target_shadow_source import BASE, ROOT, prepare
from test_placement_ablation import environment, functions

sys.path.insert(0,str(ROOT))
from analyze_target_shadow import parse


class TargetShadowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='rrzc-target-shadow-')
        cls.path=Path(cls.temp.name);prepare(cls.path/'副本')
        cls.new=cls.path/'副本/源码/协同池副本/plugin/zenfs/fs'
        cls.old=BASE/'源码/协同池副本/plugin/zenfs/fs'

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def compile(self,text,name):
        source=self.path/(name+'.cc');source.write_text(text);exe=self.path/name
        subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-Wshadow',
                        '-Wsign-compare','-Wnon-virtual-dtor','-pthread',
                        '-I'+str(self.new),str(source),'-o',str(exe)],check=True)
        return exe

    def harness(self):
        text=(ROOT/'tests/placement_native_harness.cc').read_text()
        text='#include "rrzc_target_shadow.h"\n#include "rrzc_write_survivor.h"\n'+text
        text=text.replace('inline bool Enabled() { return true; }',
                          'inline bool Enabled() { return true; } inline uint64_t Now(){return 0;}')
        text=text.replace('sources;};','sources;uint64_t owner=0;rrzc_write_survivor::State write_history=rrzc_write_survivor::State::kNew;};')
        text=text.replace('struct IOStatus {',
            'namespace rrzc_cp {enum {kMode,kAttempt,kG1Skip,kPoolTarget};template<class... A>void Emit(int,uint64_t,uint64_t,uint64_t,std::initializer_list<uint64_t> ={}){}}\n'
            '#define LIFETIME_DIFF_NOT_GOOD 100\n'
            'struct IOStatus {')
        text=text.replace('  Env::WriteLifeTimeHint lifetime_=',
                          '  std::atomic<uint64_t> used_capacity_{0};\n  Env::WriteLifeTimeHint lifetime_=')
        text=text.replace('  int logger_=0;', '  uint64_t CompositionDevice() const {return 1;}\n  int logger_=0;')
        text=text.replace('++observed;return observation;', '''++observed;
    if(auto* s=rrzc_target_shadow::current) {
      s->Begin([&]{return coloc_pool_?coloc_pool_->Snapshot():std::vector<rrzc_colocation::Member>{};});
      for(const auto& r:observation)s->Capture([&]{rrzc_target_shadow::Row v;
        v.zone=r.id;v.generation=r.generation;v.known=!r.busy;
        v.remaining=r.remaining;v.used=1;v.compatible=r.content_compatible;v.pool=s->Member(r.id);return v;});
    }
    return observation;''')
        text=text.replace('  switch(scenario) {',
            '  current.write_history=static_cast<rrzc_write_survivor::State>(std::atoi(std::getenv("测试历史")));\n  switch(scenario) {')
        # 仅重命名桩字段，避免它们与真实原函数的局部变量触发-Wshadow；原函数不改。
        text=re.sub(r'\bobservation\b','stub_observation',text)
        text=re.sub(r'\bobserved\b','stub_observed',text)
        return text

    def test_real_target_functions_preserve_all_modes_and_tokens(self):
        for fs,name,device in [(self.old,'旧真实分配函数.inc','OldDevice'),
                                (self.new,'新真实分配函数.inc','NewDevice')]:
            (self.path/name).write_text(functions((fs/'rrzc_colocation_zenfs.inc').read_text(),device))
        text=self.harness().replace('      if(mode==rrzc_placement::Mode::kG2)',
            '      assert(before==b.Result(right));\n      assert(rrzc_target_shadow::current==nullptr);\n'
            '      if(mode==rrzc_placement::Mode::kG2)')
        exe=self.compile(text,'真实目标回归')
        for mode in ('G0','G1','G2','G2_WSURVIVOR'):
            for state in range(3):
                for enabled in ('0','1'):
                    env=environment(mode);env.update(测试历史=str(state),ZENFS_RRZC_TARGET_SHADOW=enabled)
                    subprocess.run([str(exe),mode],env=env,check=True,capture_output=True)

    def test_pure_advice_ties_unknown_and_no_rc_filter(self):
        exe=self.compile(r'''#include "rrzc_target_shadow.h"
#include <cassert>
#include <iostream>
int main(){using namespace rrzc_target_shadow;
 Row a;a.zone=80;a.known=true;a.used=20;a.remaining=5;a.compatible=true;a.difference=3;
 Row b=a;b.zone=4;Row c=a;c.zone=99;c.difference=4;
 auto advice=Choose({a,b,c},5,100,true,true);
 assert(advice.has_partial && advice.complete && advice.partial.zone==4);
 advice=Choose({b,a},5,100,true,true);assert(advice.partial.zone==80);
 b.pool=true;assert(Choose({a,b},5,100,true,true).partial.zone==80);
 assert(!Choose({a},6,100,true,true).has_partial);
 a.rc_known=true;a.rc=9999;a.threshold=1;
 assert(Choose({a},5,100,true,true).has_partial);
 Row missing;missing.zone=8;
 advice=Choose({a,missing},5,100,true,true);
 assert(advice.has_partial && !advice.complete && advice.unknown==1);
 assert(!Choose({a},5,100,true,false).complete);
 assert(!Choose({},0,100,false,true).complete);
 Scope outer(true,0,5);assert(current==&outer);
 {Scope disabled(false,0,0);assert(current==nullptr);}
 assert(current==&outer);
 outer.Begin([]{throw 1;return std::vector<Row>{};});
 auto detail=outer.Describe(1,2,false,0,0,0,100,1,1);
 assert(detail.find("观察完整=0")!=std::string::npos);
 std::cout<<"目标备选影子："<<detail<<"\n";
}''','纯建议边界')
        log=subprocess.check_output([str(exe)],text=True)
        self.assertEqual(len(parse(log)['逐次']),1)
        with self.assertRaises(ValueError):parse(log.replace('未申请令牌=1','未申请令牌=0'))
        with self.assertRaises(ValueError):parse(log+log)

    def test_actual_observation_body_keeps_query_and_lock_counts(self):
        def extract(fs,name):
            text=(fs/'rrzc_colocation_zenfs.inc').read_text()
            start=text.index('std::vector<rrzc_colocation::Zone> ZonedBlockDevice::ColocationObservation(')
            end=text.index('\nIOStatus ZonedBlockDevice::RestoreColocation',start)
            return text[start:end].replace('ZonedBlockDevice::',name+'::')
        text=self.harness();text=text[:text.index('struct OldDevice:NativeBase')]
        text=text.replace('  bool ok() const {return true;}',
                          '  std::string ToString() const {return "合成状态";}\n  bool ok() const {return true;}')
        text=text.replace('enum WriteLifeTimeHint { kNormal=0 };',
                          'enum WriteLifeTimeHint { kNormal=0,WLTH_NOT_SET=0,WLTH_NONE=1,WLTH_SHORT=2,WLTH_MEDIUM=3,WLTH_LONG=4,WLTH_EXTREME=5 };')
        text=text.replace('inline bool Claimable(uint8_t a,uint8_t b)',
                          'inline bool Compatible(uint8_t a,uint8_t b){return a==0 || a==b;}\ninline bool Claimable(uint8_t a,uint8_t b)')
        native=(self.old/'zbd_zenfs.cc').read_text()
        start=native.index('unsigned int GetLifeTimeDiff(')
        end=native.index('\nIOStatus ZonedBlockDevice::AllocateMetaZone',start)
        text+='#define LIFETIME_DIFF_COULD_BE_WORSE 50\n'+native[start:end]
        text+='''
namespace rrzc_v1 {struct RecentReport {uint64_t threshold=100;struct Block{uint64_t zone,rc;};std::vector<Block> zones;};}
struct ObservedBase:NativeBase {
 std::vector<Zone*> io_zones{&zone};unsigned queries=0;
 bool IsContentGuardEnabled(){return true;}
 IOStatus GetZoneRecentReads(rrzc_v1::RecentReport* r){++queries;r->zones={{zone.start_,20}};return {};}
};
struct OldObserve:ObservedBase {std::vector<rrzc_colocation::Zone> ColocationObservation(bool);};
struct NewObserve:ObservedBase {std::vector<rrzc_colocation::Zone> ColocationObservation(bool);};
struct NativeScan:ObservedBase {
 IOStatus GetBestOpenZoneMatch(Env::WriteLifeTimeHint,unsigned*,Zone**,uint32_t,std::string*,uint8_t);
};
'''
        text+=extract(self.old,'OldObserve')+extract(self.new,'NewObserve')
        scan_begin=native.index('IOStatus ZonedBlockDevice::GetBestOpenZoneMatch(')
        scan_end=native.index('\nIOStatus ZonedBlockDevice::AllocateEmptyZone',scan_begin)
        text+=native[scan_begin:scan_end].replace('ZonedBlockDevice::','NativeScan::')
        text+='''
int main(){for(bool risk:{false,true})for(bool acquire:{false,true}) {
 OldObserve a;NewObserve b;a.zone.acquire_ok=b.zone.acquire_ok=acquire;
 const auto x=a.ColocationObservation(risk);
 {rrzc_target_shadow::Scope shadow(true,0,1);const auto y=b.ColocationObservation(risk);
  assert(x.size()==y.size());
  for(size_t i=0;i<x.size();++i) {
   const auto& l=x[i];const auto& r=y[i];
   assert(std::tie(l.id,l.generation,l.remaining,l.state,l.busy,l.reserved,l.content_compatible,l.risk_safe)==
          std::tie(r.id,r.generation,r.remaining,r.state,r.busy,r.reserved,r.content_compatible,r.risk_safe));
  }
  const auto log=shadow.Describe(1,2,false,0,0,0,100,1,1);
  assert(log.find(acquire?"未观察区数=0":"未观察区数=1")!=std::string::npos);
 }
 assert(a.zone.acquired==b.zone.acquired && a.zone.released==b.zone.released && a.queries==b.queries);
}
// 纯建议与真实普通扫描正文核对；同分遵循后扫描替换，不按Zone编号排序。
for(unsigned hint=0;hint<=5;++hint)for(unsigned minimum=0;minimum<=9;++minimum) {
 NativeScan scan;Zone a,b,c;scan.io_zones={&a,&b,&c};
 a.start_=90;b.start_=3;c.start_=40;
 a.empty=b.empty=c.empty=false;
 a.used_capacity_=20;b.used_capacity_=15;c.used_capacity_=9;
 a.capacity_=8;b.capacity_=5;c.capacity_=1;
 a.lifetime_=static_cast<Env::WriteLifeTimeHint>((hint+2)%6);
 b.lifetime_=static_cast<Env::WriteLifeTimeHint>(hint);
 c.lifetime_=static_cast<Env::WriteLifeTimeHint>((hint+3)%6);
 const auto lifetime=static_cast<Env::WriteLifeTimeHint>(hint);
 std::vector<rrzc_target_shadow::Row> rows;
 for(auto* z:scan.io_zones) {
  rrzc_target_shadow::Row r;r.zone=z->start_;r.known=true;r.compatible=true;
  r.used=z->used_capacity_.load();r.remaining=z->capacity_;
  r.difference=GetLifeTimeDiff(z->lifetime_,lifetime);rows.push_back(r);
 }
 Zone* target=nullptr;unsigned diff=100;
 scan.GetBestOpenZoneMatch(lifetime,&diff,&target,minimum,nullptr,rrzc_content::kSst);
 const auto advice=rrzc_target_shadow::Choose(rows,minimum,100,true,true);
 assert(advice.has_partial==(target!=nullptr));
 if(target)assert(advice.partial.zone==target->start_ && advice.partial.difference==diff);
}
}
'''
        exe=self.compile(text,'真实观察回归');subprocess.run([str(exe)],check=True)

    def test_only_observation_sources_changed(self):
        changed={'rrzc_colocation_zenfs.inc','zbd_zenfs.cc'}
        for old in self.old.iterdir():
            if old.is_file() and old.name not in changed:
                self.assertEqual(old.read_bytes(),(self.new/old.name).read_bytes(),old.name)
        self.assertEqual((self.new/'zbd_zenfs.cc').read_text(),
                         '#include "rrzc_target_shadow.h"\n'+(self.old/'zbd_zenfs.cc').read_text())
        patch=(self.path/'副本/代码差异.patch').read_text()
        self.assertNotIn('+    s = reserved->Finish',patch)


if __name__=='__main__':unittest.main()
