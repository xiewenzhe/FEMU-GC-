// 真实资源管理函数配合内存Zone/匹配扫描替身，不代表设备搬运或收益。
#include "migration-fixture.inc"
static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
static void partial(Zone& z,uint64_t remaining,uint64_t valid) {
  z.max_capacity_=256ULL<<20; z.capacity_=remaining; z.used_capacity_=valid;
  z.wp_=z.start_+z.max_capacity_-remaining; z.empty=false;
}
int main() {
  const uint64_t mib=1ULL<<20;
  for (const uint64_t tail:{32*mib,16*mib}) {
    ZonedBlockDevice d; Zone a,b;
    a.max_capacity_=a.capacity_=256*mib; b.max_capacity_=b.capacity_=256*mib;
    b.start_=b.wp_=512*mib; d.io_zones={&a,&b}; reserve(d);
    partial(a,tail,224*mib);
    Zone* target=take(d,32*mib);
    CHECK(target==(tail==32*mib ? &a : &b));
    CHECK(a.finishes==(tail==32*mib ? 0u : 1u));
    CHECK(a.used_capacity_==224*mib && a.resets==0);
    // 仅在内存替身中完成一次32MiB写；Finish本身没有搬运旧数据。
    target->capacity_-=32*mib; target->wp_+=32*mib;
    target->used_capacity_+=32*mib; target->empty=false;
    CHECK(d.ReleaseMigrateZone(target).ok());
    CHECK(a.used_capacity_==(tail==32*mib ? 256*mib : 224*mib));
    std::cout<<"合成尾部="<<tail/mib<<"MiB，请求32MiB，旧区Finish="<<a.finishes
             <<"，本次模拟搬运32MiB，旧有效数据未重复复制\n";
  }
  {
    ZonedBlockDevice d; Zone a,b;
    a.max_capacity_=a.capacity_=256*mib; b.max_capacity_=b.capacity_=256*mib;
    b.start_=b.wp_=512*mib; d.io_zones={&a,&b}; reserve(d);
    partial(a,16*mib,224*mib); used(b,64*mib); d.active_tokens=1;
    CHECK(take(d,32*mib)==&b && a.finishes==0 && a.capacity_==16*mib);
    CHECK(d.ReleaseMigrateZone(&b).ok());
  }
  {
    ZonedBlockDevice d; Zone a;
    a.max_capacity_=a.capacity_=256*mib; d.io_zones={&a}; reserve(d);
    partial(a,16*mib,224*mib);
    CHECK(take(d,32*mib)==nullptr && a.finishes==0 && a.capacity_==16*mib);
    CHECK(a.busy && d.migration_reserve_zone_==&a);
  }
  {
    ZonedBlockDevice d; Zone a;
    a.max_capacity_=a.capacity_=256*mib; d.io_zones={&a}; reserve(d);
    partial(a,16*mib,0);
    CHECK(take(d,32*mib)==&a && a.finishes==0 && a.resets==1);
    CHECK(d.ReleaseMigrateZone(&a).ok());
  }
  std::cout<<"预留尾部分支："<<checks<<"项检查通过（合成数据）\n";
}
