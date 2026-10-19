// 从独立补丁提取真实观察接口；Zone与设备为内存替身，不调用分配或NVMe。
#include "../model/rrzc_cost_observe.h"
#include <atomic>
#include <cassert>
#include <iostream>
#include <mutex>
#include <thread>

struct ZoneResetState { uint64_t generation=0,sequence=0; bool stable=true,known=true; };
struct Zone {
  uint64_t start_=1,capacity_=64ULL<<20,wp_=32ULL<<20;
  std::atomic<uint64_t> used_capacity_{32ULL<<20};
  mutable unsigned calls=0;
  bool reset_during=false, unknown=false;
  ZoneResetState GetResetState() const {
    ++calls; ZoneResetState s;
    if (reset_during && calls%2==0) s.sequence=2;
    if (unknown) s.known=false;
    return s;
  }
};
struct ZonedBlockDevice {
  bool migration_reserve_enabled_=true;
  std::mutex migrate_zone_mtx_,migration_reserve_mtx_;
  std::atomic<bool> migrating_{false};
  Zone* migration_reserve_zone_=nullptr;
  std::vector<Zone*> io_zones;
  std::vector<rrzc_cost::ValidityPoint> GetCostValidityReadings(const rrzc_v1::RecentReport&,uint64_t) const;
  rrzc_cost::ReserveObservation GetMigrationReserveObservation();
};
#include "cost-backend-under-test.inc"
static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
int main() {
  ZonedBlockDevice device; Zone zone;
  auto out=device.GetMigrationReserveObservation();
  CHECK(out.enabled && out.known && !out.present);
  device.migration_reserve_enabled_=false; out=device.GetMigrationReserveObservation();
  CHECK(!out.enabled && !out.known);
  device.migration_reserve_enabled_=true; device.migrating_=true;
  out=device.GetMigrationReserveObservation(); CHECK(!out.known);
  device.migrating_=false; device.migration_reserve_zone_=&zone;
  out=device.GetMigrationReserveObservation();
  CHECK(out.known && out.present && out.zone==1 && out.remaining==(64ULL<<20));
  CHECK(out.valid==(32ULL<<20) && out.wp==(32ULL<<20));
  // 用另一线程占锁，不能依赖同线程对非递归mutex的再次加锁语义。
  for (std::mutex* mutex:{&device.migrate_zone_mtx_,&device.migration_reserve_mtx_}) {
    std::atomic<bool> locked{false},release{false};
    std::thread holder([&]{std::lock_guard<std::mutex> lock(*mutex); locked=true;
      while (!release) std::this_thread::yield();});
    while (!locked) std::this_thread::yield();
    out=device.GetMigrationReserveObservation(); CHECK(!out.known);
    release=true; holder.join();
  }
  zone.reset_during=true; out=device.GetMigrationReserveObservation(); CHECK(!out.known);
  zone.reset_during=false; zone.unknown=true;
  out=device.GetMigrationReserveObservation(); CHECK(!out.known);
  zone.unknown=false;
  device.io_zones={&zone}; rrzc_v1::RecentReport report;
  rrzc_v1::RecentBlock block; block.zone=1; block.generation=7; report.zones={block};
  auto points=device.GetCostValidityReadings(report,123);
  CHECK(points.size()==1 && points[0].known && points[0].device_generation==7);
  CHECK(points[0].valid==(32ULL<<20) && points[0].time_ns==123);
  zone.reset_during=true; points=device.GetCostValidityReadings(report,124); CHECK(!points[0].known);
  zone.reset_during=false; zone.unknown=true;
  points=device.GetCostValidityReadings(report,125); CHECK(!points[0].known);
  zone.unknown=false; report.zones.clear();
  points=device.GetCostValidityReadings(report,126); CHECK(!points[0].known);
  std::cout<<"ZenFS真实成本观察接口："<<checks<<"项检查通过（测试替身）\n";
}
