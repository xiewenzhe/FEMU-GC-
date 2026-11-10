// 编译真实InitColocationPool/TryColocation函数；Zone与令牌后端为明确的本地测试桩。
// 不代表完整ZenFS链接、真实设备功能、IO性能或实验收益验证。
#include "rrzc_placement_mode.h"
#include "rrzc_colocation_pool.h"
#include <atomic>
#include <cassert>
#include <fstream>
#include <functional>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <tuple>

namespace rrzc_stream { inline bool Enabled() { return true; } }
namespace rrzc_content {
constexpr uint8_t kSst=1;
inline bool Claimable(uint8_t a,uint8_t b) { return a==0 || a==b; }
}
namespace rrzc_colocation {
struct Context {Tag tag=Tag::kNormal;std::set<uint64_t> sources;};
inline Context current;
}
struct IOStatus {
  bool ok() const {return true;}
  static IOStatus OK() {return {};}
};
struct Env {enum WriteLifeTimeHint { kNormal=0 };};
template<class... A> void Info(int,const char*,A...) {}
struct Zone {
  uint64_t start_=7,capacity_=128,generation=1;
  std::atomic<uint8_t> rrzc_content_{0};
  Env::WriteLifeTimeHint lifetime_=Env::kNormal;
  bool empty=true,full=false,acquire_ok=true;
  unsigned acquired=0,released=0;
  std::function<void()> at_acquire;
  bool Acquire(){++acquired;if(at_acquire)at_acquire();return acquire_ok;}
  IOStatus CheckRelease(){++released;return {};}
  struct Generation {bool known=true,stable=true;uint64_t generation=1;};
  Generation GetResetState(){return {true,true,generation};}
  bool IsFull(){return full;}
  bool IsEmpty(){return empty;}
};
struct NativeBase {
  int logger_=0;
  unsigned max_nr_active_io_zones_=10,max_nr_open_io_zones_=8;
  std::atomic<long> active_io_zones_{0},open_io_zones_{0};
  std::mutex zone_resources_mtx_;
  rrzc_placement::Mode placement_mode_=rrzc_placement::Mode::kLegacy;
  std::unique_ptr<rrzc_colocation::Pool> coloc_pool_;
  std::map<std::string,uint8_t> coloc_tags_;
  std::set<Zone*> coloc_migration_targets_;
  std::vector<rrzc_colocation::Zone> observation;
  Zone zone;
  unsigned observed=0,get_active=0,put_active=0,put_open=0;
  bool active_ok=true;
  std::vector<rrzc_colocation::Zone> ColocationObservation(bool){++observed;return observation;}
  Zone* GetIOZone(uint64_t z){return z==zone.start_?&zone:nullptr;}
  bool GetActiveIOZoneTokenIfAvailable(){++get_active;if(active_ok)++active_io_zones_;return active_ok;}
  void PutActiveIOZoneToken(){++put_active;--active_io_zones_;}
  void PutOpenIOZoneToken(){++put_open;--open_io_zones_;}
  std::string Result(Zone* target) {
    std::ostringstream out;
    out<<(target?target->start_:0)<<'/'<<observed<<'/'<<get_active<<'/'<<put_active
       <<'/'<<put_open<<'/'<<active_io_zones_<<'/'<<open_io_zones_
       <<'/'<<zone.acquired<<'/'<<zone.released<<'/'<<coloc_migration_targets_.size();
    if(coloc_pool_)for(const auto& m:coloc_pool_->Snapshot())out<<'/'<<m.zone<<':'<<int(m.observed_tags);
    return out.str();
  }
};
struct OldDevice:NativeBase {
  void InitColocationPool();
  IOStatus TryColocation(uint32_t,Env::WriteLifeTimeHint,bool,Zone**);
};
struct NewDevice:NativeBase {
  void InitColocationPool();
  IOStatus TryColocation(uint32_t,Env::WriteLifeTimeHint,bool,Zone**);
};
// 两个文件由Python从归档源和新副本中提取实际函数，未在测试中重写分配逻辑。
#include "旧真实分配函数.inc"
#include "新真实分配函数.inc"

template<class Device> void Prepare(Device& d,unsigned scenario) {
  using namespace rrzc_colocation;
  d.observation={{7,1,128,State::kEmpty,false,false,true,true}};
  current={Tag::kShortLived,{}};
  if(scenario>=12) {
    d.zone.empty=false;d.zone.rrzc_content_=1;
    d.observation[0].state=State::kClosed;
    if(d.coloc_pool_) {
      auto initial=d.coloc_pool_->Choose(d.observation,{10,10,7},Tag::kShortLived,1);
      // 建立已有成员时先用Empty，随后变成Closed。
      d.observation[0].state=State::kEmpty;
      initial=d.coloc_pool_->Choose(d.observation,{10,10,7},Tag::kShortLived,1);
      assert(d.coloc_pool_->Commit(initial));d.observation[0].state=State::kClosed;
    }
    current.tag=scenario==15?Tag::kBoth:Tag::kReadHot;
  }
  switch(scenario) {
    case 1:current.tag=Tag::kNormal;break;
    case 2:d.observation[0].risk_safe=false;break;
    case 3:d.observation[0].remaining=0;break;
    case 4:d.open_io_zones_=7;break;
    case 5:d.active_io_zones_=10;break;
    case 6:d.zone.acquire_ok=false;break;
    case 7:d.zone.generation=2;break;
    case 8:d.zone.rrzc_content_=2;break;
    case 9:d.zone.capacity_=0;break;
    case 10:d.active_ok=false;break;
    case 11:d.zone.at_acquire=[&d]{if(d.coloc_pool_)d.coloc_pool_->SetLimit(1);};break;
    case 13:current.sources.insert(7);break;
    case 14:d.observation[0].busy=true;break;
    case 16:current.tag=Tag::kShortLived;d.zone.at_acquire=[&d]{d.open_io_zones_=7;};break;
    default:break;
  }
}

int main(int argc,char** argv) {
  assert(argc==2);
  const auto mode=rrzc_placement::ProcessSettings().mode;
  assert(rrzc_placement::Name(mode)==std::string(argv[1]));
  unsigned equal=0;
  for(unsigned scenario=0;scenario<17;++scenario) {
    for(bool new_file:{false,true}) {
      OldDevice a;NewDevice b;
      // 仅为旧函数测试桩提供其原参数；新代码读取的进程模式已固定，不会切换。
      setenv("ZENFS_RRZC_POOL_K","1",1);a.InitColocationPool();unsetenv("ZENFS_RRZC_POOL_K");
      b.InitColocationPool();Prepare(a,scenario);Zone* left=nullptr;
      a.TryColocation(1,Env::kNormal,new_file,&left);
      const auto before=a.Result(left);
      Prepare(b,scenario);Zone* right=nullptr;
      b.TryColocation(1,Env::kNormal,new_file,&right);
      if(mode==rrzc_placement::Mode::kG2) {assert(before==b.Result(right));++equal;}
      if(mode==rrzc_placement::Mode::kG0) {assert(!right && b.observed==0 && b.get_active==0);}
      if(mode==rrzc_placement::Mode::kG1 && !new_file && scenario>=12 && scenario<=15)
        assert(!right && b.observed==0 && b.get_active==0 && b.zone.acquired==0);
      if(mode==rrzc_placement::Mode::kG1 && (new_file || scenario<12 || scenario==16))
        assert(before==b.Result(right));
      assert(rrzc_colocation::current.tag==(scenario>=12 && scenario<=15?
          (scenario==15?rrzc_colocation::Tag::kBoth:rrzc_colocation::Tag::kReadHot):
          scenario==1?rrzc_colocation::Tag::kNormal:rrzc_colocation::Tag::kShortLived));
    }
  }
  std::cout<<"模式="<<argv[1]<<"，场景=34，G2与旧分配函数一致="<<equal<<"\n";
}
