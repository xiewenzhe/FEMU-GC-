// 在内存替身上编译同一修复函数，验证角色、busy及计数边界，不冒充设备实验。
#include "rrzc_zone_content.h"
#include <atomic>
#include <cassert>
#include <iostream>
#include <mutex>
#include <vector>
struct IOStatus {
  bool value=true;
  bool ok() const {return value;}
  static IOStatus OK(){return {};}
  static IOStatus Corruption(const char*){return {false};}
  static IOStatus IOError(const char*){return {false};}
};
struct Zone {
  uint64_t start_=0;
  std::atomic<uint64_t> used_capacity_{1};
  std::atomic<uint8_t> rrzc_content_{rrzc_content::kSst};
  bool busy=false,empty=false,full=false;
  bool Acquire(){if(busy)return false;busy=true;return true;}
  IOStatus CheckRelease(){if(!busy)return {false};busy=false;return {};}
  bool IsEmpty() const{return empty;}
  bool IsFull() const{return full;}
};
static void Info(void*,const char*,...) __attribute__((format(printf,2,3)));
static void Info(void*,const char*,...){ }
struct ZonedBlockDevice {
  bool migration_reserve_enabled_=true,guard=true;
  std::atomic<bool> migrating_{false};
  std::atomic<int64_t> active_io_zones_{3};
  std::mutex migrate_zone_mtx_,migration_reserve_mtx_;
  Zone* migration_reserve_zone_=nullptr;
  Zone* migration_spare_zone_=nullptr;
  std::vector<Zone*> io_zones;
  void* logger_=nullptr;
  bool IsContentGuardEnabled() const{return guard;}
  IOStatus ReconcileMigrationReserveContent();
};
#include "rrzc_reserve_restore.inc"
int main(){
  Zone metadata,sst,full,spare;
  metadata.start_=6;metadata.rrzc_content_=rrzc_content::kOther;metadata.busy=true;
  metadata.used_capacity_=134674;sst.start_=7;sst.used_capacity_=6525646;
  full.full=true;spare.empty=true;spare.used_capacity_=0;spare.busy=true;
  ZonedBlockDevice d;d.migration_reserve_zone_=&metadata;d.migration_spare_zone_=&spare;
  d.io_zones={&metadata,&full,&spare,&sst};
  assert(d.ReconcileMigrationReserveContent().ok());
  assert(d.migration_reserve_zone_==&sst && !metadata.busy && sst.busy && spare.busy);
  assert(d.active_io_zones_==3 && metadata.used_capacity_==134674 && sst.used_capacity_==6525646);
  assert(d.ReconcileMigrationReserveContent().ok());
  d.migration_reserve_zone_=&metadata;metadata.busy=true;
  assert(!d.ReconcileMigrationReserveContent().ok());
  assert(metadata.busy && sst.busy && d.active_io_zones_==3);
  d.guard=false;assert(d.ReconcileMigrationReserveContent().ok());
  d.guard=true;d.migrating_=true;assert(!d.ReconcileMigrationReserveContent().ok());
  d.migrating_=false;metadata.rrzc_content_=rrzc_content::kUnknown;
  assert(d.ReconcileMigrationReserveContent().ok());
  std::cout<<"挂载预留角色交换、数据计数不变、busy归属、无替补及在途边界通过\n";
}
