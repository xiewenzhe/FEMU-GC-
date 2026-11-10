"""真实普通目标函数回归、H排序、本批隔离和故障路径，不启动业务实验。"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from placement_hotfallback_source import prepare,BASE


def extract(text):
    start=text.index('IOStatus ZonedBlockDevice::GetBestOpenZoneMatch(')
    begin=text.index('{',start);depth=1;end=begin+1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]


class HotFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='rrzc-hotfallback-')
        cls.path=Path(cls.temp.name);prepare(cls.path/'副本')
        cls.fs=cls.path/'副本/源码/协同池副本/plugin/zenfs/fs'

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_actual_allocator_regression_and_new_ranking(self):
        before=extract((BASE/'源码/协同池副本/plugin/zenfs/fs/zbd_zenfs.cc').read_text())
        after=extract((self.fs/'zbd_zenfs.cc').read_text())
        (self.path/'旧函数.inc').write_text(before.replace('ZonedBlockDevice::','Old::'))
        (self.path/'新函数.inc').write_text(after.replace('ZonedBlockDevice::','New::'))
        code=r'''#include "rrzc_hot_fallback.h"
#include "rrzc_colocation_pool.h"
#include <atomic>
#include <cassert>
#include <sstream>
#include <string>
#include <vector>
using namespace rrzc_placement;
namespace rrzc_colocation {struct C {Tag tag=Tag::kReadHot;std::set<uint64_t> sources;uint64_t owner=1;};inline C current;}
namespace rrzc_content {constexpr uint8_t kSst=1;inline bool Compatible(uint8_t a,uint8_t b){return a==b;}}
struct IOStatus {int error=0;bool ok()const{return !error;}static IOStatus OK(){return {};}};
struct Env {using WriteLifeTimeHint=unsigned;};
constexpr unsigned LIFETIME_DIFF_NOT_GOOD=100;
struct Zone {uint64_t start_,capacity_;std::atomic<uint64_t> used_capacity_;unsigned lifetime_=1;
 std::atomic<uint8_t> rrzc_content_{1};int held=0;bool busy=false,fail=false;
 Zone(uint64_t id,uint64_t used,uint64_t cap):start_(id),capacity_(cap),used_capacity_(used){}
 bool Acquire(){if(busy)return false;assert(!held);held=1;return true;}
 IOStatus CheckRelease(){assert(held);held=0;return {fail?1:0};}
 bool IsFull()const{return capacity_==0;} bool IsEmpty()const{return used_capacity_==0;}
 struct G {uint64_t generation;};G GetResetState()const{return {0};}};
struct Pool {bool Contains(uint64_t z){return z==999;}};
template<class... A> void Info(int,const char*,A...){}
struct Common {std::vector<Zone*> io_zones;Pool* coloc_pool_=nullptr;int logger_=0;
 Mode placement_mode_=Mode::kG2;
 unsigned GetLifeTimeDiff(unsigned a,unsigned b){return a>b?a-b:b-a;}
 bool IsContentGuardEnabled()const{return true;}uint64_t CompositionDevice()const{return 1;}};
struct Old:Common {IOStatus GetBestOpenZoneMatch(unsigned,unsigned*,Zone**,uint32_t,std::string*,uint8_t);};
struct New:Common {IOStatus GetBestOpenZoneMatch(unsigned,unsigned*,Zone**,uint32_t,std::string*,uint8_t,bool=false);};
#include "旧函数.inc"
#include "新函数.inc"
int main(){
 for(unsigned a=0;a<4;++a)for(unsigned b=0;b<4;++b){
  Zone x(10,80,100),y(20,20,100);x.lifetime_=a;y.lifetime_=b;
  Old old;New fresh;old.io_zones=fresh.io_zones={&x,&y};Zone *p=nullptr,*q=nullptr;unsigned u,v;
  auto s=old.GetBestOpenZoneMatch(1,&u,&p,10,nullptr,1);if(p)p->CheckRelease();
  auto t=fresh.GetBestOpenZoneMatch(1,&v,&q,10,nullptr,1,true);if(q)q->CheckRelease();
  assert(s.error==t.error && p==q && u==v);
 }
 Zone x(10,80,100),y(20,20,100);New device;device.io_zones={&x,&y};
 device.placement_mode_=Mode::kG2HotFallback;Zone* chosen=nullptr;unsigned diff;
 rrzc_hot_fallback::Batch batch(true,1);batch.Sources({777});
 device.GetBestOpenZoneMatch(1,&diff,&chosen,10,nullptr,1,true);assert(chosen==&y);chosen->CheckRelease();
 batch.Hot(20,0);rrzc_colocation::current.tag=rrzc_colocation::Tag::kNormal;
 device.GetBestOpenZoneMatch(1,&diff,&chosen,10,nullptr,1,true);assert(chosen==&x);chosen->CheckRelease();
 x.busy=true;device.GetBestOpenZoneMatch(1,&diff,&chosen,10,nullptr,1,true);
 assert(chosen==&y);chosen->CheckRelease();x.busy=false;
 rrzc_colocation::current.tag=rrzc_colocation::Tag::kBoth;
 rrzc_colocation::current.sources={20};device.GetBestOpenZoneMatch(1,&diff,&chosen,10,nullptr,1,true);
 assert(chosen==&x);chosen->CheckRelease();rrzc_colocation::current.sources.clear();
 y.capacity_=5;device.GetBestOpenZoneMatch(1,&diff,&chosen,10,nullptr,1,true);
 assert(chosen==&x);chosen->CheckRelease();y.capacity_=100;
 y.rrzc_content_=2;device.GetBestOpenZoneMatch(1,&diff,&chosen,10,nullptr,1,true);
 assert(chosen==&x);chosen->CheckRelease();y.rrzc_content_=1;
 Pool pool;Zone member(999,1,100);device.coloc_pool_=&pool;device.io_zones.push_back(&member);
 device.GetBestOpenZoneMatch(1,&diff,&chosen,10,nullptr,1,true);assert(chosen==&y);chosen->CheckRelease();
 // 普通新文件分配不使用迁移偏好；原同分后者优先的路径保持。
 device.io_zones={&y,&x};device.GetBestOpenZoneMatch(1,&diff,&chosen,10,nullptr,1,false);
 assert(chosen==&x);chosen->CheckRelease();
 // 原释放失败不能被排序掩盖。
 x.fail=true;device.io_zones={&x,&y};auto failure=device.GetBestOpenZoneMatch(1,&diff,&chosen,10,nullptr,1,true);
 assert(!failure.ok() && !x.held && !y.held);
 assert(batch.Avoid(20,0) && !batch.Avoid(20,1));
 {rrzc_hot_fallback::Batch nested(true,2);assert(rrzc_hot_fallback::current->Device()==2);}
 assert(rrzc_hot_fallback::current==&batch);
}'''
        file=self.path/'真实函数回归.cc';file.write_text(code);exe=self.path/'真实函数回归'
        subprocess.run(['g++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-pthread',
                        '-I'+str(self.fs),'-I'+str(self.path),str(file),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)

    def test_scope_and_unchanged_core(self):
        old=BASE/'源码/协同池副本/plugin/zenfs/fs'
        for name in ['io_zenfs.cc','io_zenfs.h','rrzc_stream_profile.h','rrzc_colocation_pool.h','rrzc_placement_trace.h']:
            self.assertEqual((old/name).read_bytes(),(self.fs/name).read_bytes())
        source=(self.fs/'fs_zenfs.cc').read_text();self.assertIn('placement_batch.Sources(source_zones)',source)
        self.assertEqual(source[source.index('IOStatus ZenFS::MigrateFileExtents('):],
                         (old/'fs_zenfs.cc').read_text()[(old/'fs_zenfs.cc').read_text().index('IOStatus ZenFS::MigrateFileExtents('):])
        identity=json.loads((self.path/'副本/源码/二阶副本身份.json').read_text())
        self.assertFalse(identity['H普通回退']['叠加W禁止'])

    def test_control_path_limit_before_guest_start(self):
        from run_placement_hotfallback_host import check_control_path,PAIR_LABEL
        check_control_path(PAIR_LABEL)
        check_control_path('joint-controlled-placement-wmig-hf-r1-20261109')
        with self.assertRaisesRegex(ValueError,'套接字'):
            check_control_path('joint-controlled-placement-wmig-pair-hotfallback-v1-20261109')


if __name__=='__main__':unittest.main()
