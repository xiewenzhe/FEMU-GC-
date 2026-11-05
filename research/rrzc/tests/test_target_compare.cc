// 使用真实比较核与适配函数体；合成Zone后端不代表真实设备验收。
#include "rrzc_target_compare.h"
#include "rrzc_colocation_scope.h"
#include <atomic>
#include <cassert>
#include <cstring>
#include <string>

struct IOStatus {
  bool success=true;
  bool ok() const{return success;}
  static IOStatus OK(){return {};}
};
namespace Env {enum WriteLifeTimeHint{kLong};}
namespace rrzc_content {constexpr uint8_t kSst=1;}
constexpr unsigned int LIFETIME_DIFF_NOT_GOOD=100;
template<class... Args>void Info(void*,const char*,Args...){ }
struct ResetState {bool known=true,stable=true;};
struct Zone {
  uint64_t start_=0,capacity_=0;
  std::atomic<uint64_t> used_capacity_{0};
  std::atomic<uint8_t> rrzc_content_{1};
  bool held=true,empty=false,full=false;
  int releases=0;
  ResetState GetResetState(){return {};}
  bool IsFull(){return full;}
  bool IsEmpty(){return empty;}
  IOStatus CheckRelease(){assert(held);held=false;++releases;return {};}
};
struct ZonedBlockDevice {
  bool target_compare_=false,target_observe_=false,pair_admission_=false,pair_observe_=false;
  std::atomic<uint64_t> target_sequence_{0};rrzc_pair::Ledger pair_ledger_;
  void* logger_=nullptr;
  Zone* available=nullptr;bool reads_ok=true,change_ledger=false;
  rrzc_v1::RecentReport report;
  int changed_owner=0;
  void InitTargetCompare();
  IOStatus CompareMigrationTargets(Zone*,uint32_t,Env::WriteLifeTimeHint,Zone**);
  IOStatus GetBestOpenZoneMatch(Env::WriteLifeTimeHint,unsigned int*,Zone** out,
      uint32_t,std::string*,uint8_t){*out=available;return {};}
  IOStatus GetZoneRecentReads(rrzc_v1::RecentReport* out){
    *out=report;
    if(change_ledger)pair_ledger_.Publish(&changed_owner,0,{{999,1}});
    return {reads_ok};
  }
  bool IsContentGuardEnabled(){return true;}
};
#include "rrzc_target_compare_zenfs.inc"

static void Policy() {
  rrzc_target::Target pool,ordinary;
  pool.available=ordinary.available=true;pool.zone=7;ordinary.zone=8;
  pool.native_valid=pool.live.total=30;pool.live.h=10;pool.live.w=20;pool.remaining=100;
  ordinary.native_valid=ordinary.live.total=40;ordinary.live.h=10;ordinary.remaining=90;
  // 普通区非W数据也算成本，不因为W为0就认定它便宜。
  assert(!rrzc_target::Compare(pool,ordinary,8).ordinary);
  ordinary.native_valid=ordinary.live.total=15;
  assert(rrzc_target::Compare(pool,ordinary,8).ordinary);
  ordinary.remaining=101;assert(!rrzc_target::Compare(pool,ordinary,8).ordinary);
  ordinary.remaining=90;ordinary.live.revision=1;
  assert(!rrzc_target::Compare(pool,ordinary,8).known);
  ordinary.live.revision=0;ordinary.native_valid=16;
  assert(!rrzc_target::Compare(pool,ordinary,8).known);
  ordinary.native_valid=15;ordinary.remaining=7;
  assert(!rrzc_target::Compare(pool,ordinary,8).known);
  ordinary.remaining=90;ordinary.zone=pool.zone;
  assert(!rrzc_target::Compare(pool,ordinary,8).known);
}

static void Adapter() {
  unsetenv("ZENFS_RRZC_POOL_TARGET_COMPARE");unsetenv("ZENFS_RRZC_POOL_TARGET_OBSERVE");
  {ZonedBlockDevice z;z.InitTargetCompare();assert(!z.target_observe_ && !z.pair_observe_);}
  setenv("ZENFS_RRZC_POOL_TARGET_COMPARE","x",1);
  try {ZonedBlockDevice z;z.InitTargetCompare();assert(false);}catch(const std::invalid_argument&){}
  setenv("ZENFS_RRZC_POOL_TARGET_COMPARE","1",1);
  try {ZonedBlockDevice z;z.pair_admission_=true;z.InitTargetCompare();assert(false);}catch(const std::invalid_argument&){}
  unsetenv("ZENFS_RRZC_POOL_TARGET_COMPARE");
  for(int mode=0;mode<7;++mode) {
    ZonedBlockDevice z;z.target_compare_=mode!=0;z.target_observe_=true;
    Zone pool,ordinary;pool.start_=7;ordinary.start_=8;
    pool.used_capacity_=30;pool.capacity_=100;ordinary.used_capacity_=15;ordinary.capacity_=90;
    int a=0,b=0;z.pair_ledger_.Publish(&a,2,{{7,30}});z.pair_ledger_.Publish(&b,0,{{8,15}});
    z.available=mode==2?nullptr:&ordinary;z.report.threshold=1024;
    rrzc_v1::RecentBlock block;block.zone=8;block.rc=mode==3?1024:20;z.report.zones.push_back(block);
    z.change_ledger=mode==4;z.reads_ok=mode!=5;
    if(mode==6)rrzc_colocation::current.sources.insert(8);
    Zone* chosen=nullptr;
    assert(z.CompareMigrationTargets(&pool,8,Env::kLong,&chosen).ok());
    assert(pool.held && pool.releases==0);
    if(mode==1){assert(chosen==&ordinary && ordinary.held && !ordinary.releases);ordinary.CheckRelease();}
    else {assert(chosen==nullptr);if(mode!=2)assert(!ordinary.held && ordinary.releases==1);}
    rrzc_colocation::current.sources.clear();
  }
}
int main(){Policy();Adapter();}
