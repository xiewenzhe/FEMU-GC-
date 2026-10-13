// 使用生产资源管理函数、内存 Zone 替身，验证独占权与普通活跃令牌。
// 不模拟设备命令、映射持久化或 NAND；并发测试仅覆盖空区分配互斥。
#include <atomic>
#include <cassert>
#include <condition_variable>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

struct IOStatus {
  bool success = true;
  bool ok() const { return success; }
  static IOStatus OK() { return {}; }
  static IOStatus NoSpace(const char*) { return {false}; }
};
struct Env { enum WriteLifeTimeHint { WLTH_MEDIUM = 3 }; };
constexpr unsigned LIFETIME_DIFF_NOT_GOOD = 100;
template <typename... T> void Info(void*, const char*, T...) {}
struct Zone {
  std::atomic<bool> busy{false};
  std::atomic<uint64_t> used_capacity_{0};
  uint64_t start_ = 256, wp_ = 256, capacity_ = 4096, max_capacity_ = 4096;
  Env::WriteLifeTimeHint lifetime_ = Env::WLTH_MEDIUM;
  bool empty = true, fail_close = false, fail_finish = false, fail_reset = false;
  unsigned closes = 0, finishes = 0, resets = 0;
  bool Acquire() { bool expected = false; return busy.compare_exchange_strong(expected, true); }
  IOStatus CheckRelease() { assert(busy.exchange(false)); return {}; }
  bool IsEmpty() { return empty; }
  bool IsFull() { return capacity_ == 0; }
  bool IsUsed() { return used_capacity_ > 0; }
  IOStatus Close() { assert(busy); ++closes; return {!fail_close}; }
  IOStatus Finish() {
    assert(busy); ++finishes;
    if (fail_finish) return {false};
    capacity_ = 0; wp_ = start_ + max_capacity_; empty = false; return {};
  }
  IOStatus Reset() {
    assert(busy && !IsUsed()); ++resets;
    if (fail_reset) return {false};
    capacity_ = max_capacity_; wp_ = start_; empty = true; return {};
  }
};
struct ZonedBlockDevice {
  std::vector<Zone*> io_zones;
  bool migration_reserve_enabled_ = true;
  std::mutex migration_reserve_mtx_, migrate_zone_mtx_;
  std::condition_variable migrate_resource_;
  std::atomic<bool> migrating_{false};
  Zone* migration_reserve_zone_ = nullptr;
  void* logger_ = nullptr;
  unsigned active_tokens = 0;
  bool fail_scan = false;
  void PutActiveIOZoneToken() { assert(active_tokens > 0); --active_tokens; }
  IOStatus GetBestOpenZoneMatch(Env::WriteLifeTimeHint, unsigned*, Zone** out, uint32_t min) {
    *out = nullptr;
    if (fail_scan) return {false};
    for (auto* z : io_zones) {
      if (!z->Acquire()) continue;
      if (z->IsUsed() && !z->IsFull() && z->capacity_ >= min) { *out = z; break; }
      z->CheckRelease();
    }
    return {};
  }
  IOStatus EnsureMigrationReserve();
  IOStatus ResetUnusedIOZones();
  IOStatus AllocateEmptyZone(Zone**, std::string* = nullptr);
  IOStatus TakeMigrateZone(Zone**, Env::WriteLifeTimeHint, uint32_t);
  IOStatus ReleaseMigrateZone(Zone*);
  IOStatus PrepareReadReclaimSource(Zone*, uint64_t, uint64_t, bool*, uint64_t*);
};
#include "migration-under-test.inc"

static void used(Zone& z, uint64_t remaining = 2048) {
  z.empty = false; z.used_capacity_ = 1024; z.capacity_ = remaining;
  z.wp_ = z.start_ + z.max_capacity_ - remaining;
}
static void reserve(ZonedBlockDevice& d) {
  std::lock_guard<std::mutex> lock(d.migration_reserve_mtx_);
  assert(d.EnsureMigrationReserve().ok());
}
static Zone* take(ZonedBlockDevice& d, uint32_t bytes = 512) {
  Zone* out = nullptr;
  assert(d.TakeMigrateZone(&out, Env::WLTH_MEDIUM, bytes).ok());
  return out;
}
int main() {
  unsigned passed = 0;
  {
    ZonedBlockDevice d; Zone a; d.io_zones = {&a}; d.migration_reserve_enabled_ = false;
    assert(take(d) == nullptr && !d.migrating_);
    Zone* out = nullptr; assert(d.AllocateEmptyZone(&out).ok() && out == &a);
    out->CheckRelease(); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a; d.io_zones = {&a};
    Zone* out = nullptr; assert(d.AllocateEmptyZone(&out).ok() && out == nullptr);
    assert(d.migration_reserve_zone_ == &a && a.busy);
    assert(take(d) == &a); used(a);
    assert(d.ReleaseMigrateZone(&a).ok() && a.busy && a.closes == 1 && !d.migrating_);
    assert(take(d) == &a); assert(d.ReleaseMigrateZone(&a).ok()); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a, b; d.io_zones = {&a, &b};
    Zone *x = nullptr, *y = nullptr;
    std::thread t1([&] { assert(d.AllocateEmptyZone(&x).ok()); });
    std::thread t2([&] { assert(d.AllocateEmptyZone(&y).ok()); });
    t1.join(); t2.join();
    assert((x == nullptr) != (y == nullptr));
    assert(d.migration_reserve_zone_ == &a && a.busy && b.busy);
    b.CheckRelease(); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a, b; d.io_zones = {&a, &b}; used(b); d.active_tokens = 1;
    reserve(d); assert(take(d) == &b);
    b.capacity_ = 0;
    assert(d.ReleaseMigrateZone(&b).ok());
    assert(d.active_tokens == 0 && !b.busy && a.busy); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a, b; d.io_zones = {&a, &b};
    reserve(d); assert(take(d) == &a); used(a, 0);
    assert(d.ReleaseMigrateZone(&a).ok());
    assert(!a.busy && b.busy && d.migration_reserve_zone_ == &b && d.active_tokens == 0);
    ++passed;
  }
  {
    ZonedBlockDevice d; Zone a, b; d.io_zones = {&a, &b};
    reserve(d); used(a, 256);
    assert(take(d) == &b && a.finishes == 1 && !a.busy);
    assert(d.ReleaseMigrateZone(&b).ok()); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a; d.io_zones = {&a}; reserve(d);
    a.empty = false; a.capacity_ = 256;
    assert(take(d) == &a && a.resets == 1 && a.finishes == 0);
    assert(d.ReleaseMigrateZone(&a).ok()); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a, b; d.io_zones = {&a, &b}; reserve(d); used(a, 256); a.fail_finish = true;
    Zone* out = nullptr;
    assert(!d.TakeMigrateZone(&out, Env::WLTH_MEDIUM, 512).ok());
    assert(out == nullptr && !d.migrating_ && a.busy && a.capacity_ == 256);
    assert(!b.busy && d.migration_reserve_zone_ == &a); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a; d.io_zones = {&a}; reserve(d);
    Zone* out = nullptr;
    assert(!d.TakeMigrateZone(&out, Env::WLTH_MEDIUM, 8192).ok());
    assert(out == nullptr && !d.migrating_ && a.busy && a.finishes == 0); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a; d.io_zones = {&a}; used(a, 0);
    reserve(d); assert(d.migration_reserve_zone_ == nullptr);
    a.used_capacity_ = 0;
    assert(d.ResetUnusedIOZones().ok() && d.migration_reserve_zone_ == &a && a.busy);
    assert(a.resets == 1 && d.active_tokens == 0); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a, b; d.io_zones = {&a, &b}; used(b); d.active_tokens = 1;
    reserve(d); assert(take(d) == &b); b.fail_close = true;
    assert(!d.ReleaseMigrateZone(&b).ok() && !b.busy && !d.migrating_);
    assert(d.active_tokens == 1); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a; d.io_zones = {&a}; reserve(d);
    d.fail_scan = true; Zone* out = nullptr;
    assert(!d.TakeMigrateZone(&out, Env::WLTH_MEDIUM, 512).ok() && !d.migrating_);
    assert(a.busy && out == nullptr); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a; d.io_zones = {&a}; reserve(d);
    assert(take(d) == &a); used(a, 0);
    assert(d.ReleaseMigrateZone(&a).ok() && d.migration_reserve_zone_ == nullptr);
    assert(take(d) == nullptr && !d.migrating_);
    a.used_capacity_ = 0;
    assert(d.ResetUnusedIOZones().ok() && d.migration_reserve_zone_ == &a); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a; d.io_zones = {&a}; reserve(d);
    a.empty = false; a.capacity_ = 256; a.fail_reset = true;
    Zone* out = nullptr;
    assert(!d.TakeMigrateZone(&out, Env::WLTH_MEDIUM, 512).ok());
    assert(out == nullptr && a.busy && !d.migrating_ && a.capacity_ == 256); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a, b; d.io_zones = {&a, &b}; used(b); d.active_tokens = 1;
    reserve(d); assert(take(d) == &b);
    assert(d.ReleaseMigrateZone(&b).ok() && !b.busy && b.closes == 1);
    assert(d.active_tokens == 1 && a.busy); ++passed;
  }
  {
    ZonedBlockDevice d; Zone a; d.io_zones = {&a}; used(a); d.active_tokens = 1;
    d.migration_reserve_enabled_ = false;
    assert(take(d) == &a); a.capacity_ = 0;
    assert(d.ReleaseMigrateZone(&a).ok() && !a.busy && a.closes == 0);
    assert(d.active_tokens == 1); ++passed;
  }
  for (unsigned scenario = 0; scenario < 12; ++scenario) {
    ZonedBlockDevice d; Zone a, b;
    b.start_ = b.wp_ = 8192;
    d.io_zones = {&a, &b};
    const bool reserved = scenario != 1 && scenario != 2 && scenario != 4 &&
                          scenario != 6 && scenario != 8;
    if (reserved) reserve(d);
    used(a, scenario == 2 ? 0 : 2048);
    if (!reserved && scenario != 2) d.active_tokens = 1;
    if (scenario == 3 || scenario == 4) a.wp_++;
    if (scenario == 5) d.migrating_ = true;
    if (scenario == 6) assert(a.Acquire());
    if (scenario == 7 || scenario == 8) a.fail_finish = true;
    if (scenario == 9) {
      a.used_capacity_ = 0; a.empty = true;
      a.wp_ = a.start_; a.capacity_ = a.max_capacity_;
    }
    if (scenario == 10) { used(b, 0); }
    bool prepared = true; uint64_t sacrificed = 123;
    uint64_t expected_wp = a.wp_ - ((scenario == 3 || scenario == 4) ? 1 : 0);
    auto s = d.PrepareReadReclaimSource(&a, 1024, expected_wp, &prepared, &sacrificed);
    assert(s.ok() == (scenario != 7 && scenario != 8));
    const bool expected_prepared = scenario == 0 || scenario == 1 || scenario == 2 ||
                                  scenario == 10 || scenario == 11;
    assert(prepared == expected_prepared);
    assert(sacrificed == (expected_prepared && scenario != 2 ? 2048u : 0u));
    if (expected_prepared) {
      assert(a.IsFull() && !a.busy && d.active_tokens == 0);
      if (reserved) {
        assert(d.migration_reserve_zone_ == (scenario == 10 ? nullptr : &b));
      }
    } else {
      assert(a.capacity_ == (scenario == 9 ? 4096u : 2048u) &&
             a.busy == (reserved || scenario == 6));
      assert(d.active_tokens == (reserved ? 0u : 1u));
      if (reserved) assert(d.migration_reserve_zone_ == &a);
    }
    if (scenario == 11) {
      // 模拟迁移安装映射后的源区清空，再核验重置不重复归还令牌。
      assert(take(d) == &b); used(b);
      assert(d.ReleaseMigrateZone(&b).ok());
      a.used_capacity_ = 0;
      assert(d.ResetUnusedIOZones().ok() && a.IsEmpty() && d.active_tokens == 0);
    }
    ++passed;
  }
  {
    // 对照真实日志的尺寸：没有替补时不能丢弃仍可容纳较小片段的尾部。
    ZonedBlockDevice d; Zone a; a.max_capacity_ = a.capacity_ = 268435456;
    d.io_zones = {&a}; reserve(d); used(a, 39916032);
    const auto original_wp = a.wp_;
    assert(take(d, 66791424) == nullptr && !d.migrating_);
    assert(a.finishes == 0 && a.capacity_ == 39916032 && a.wp_ == original_wp);
    assert(a.busy && d.migration_reserve_zone_ == &a && d.active_tokens == 0);
    assert(take(d, 17697280) == &a);
    assert(d.ReleaseMigrateZone(&a).ok()); ++passed;
  }
  for (unsigned scenario = 0; scenario < 2; ++scenario) {
    // 候选已占用或已满均不是替补，不释放其他使用者持有的独占权。
    ZonedBlockDevice d; Zone a, b; d.io_zones = {&a, &b}; reserve(d); used(a, 256);
    if (scenario == 0) assert(b.Acquire());
    else used(b, 0);
    assert(take(d, 512) == nullptr && !d.migrating_);
    assert(a.capacity_ == 256 && a.finishes == 0 && a.busy);
    assert(d.migration_reserve_zone_ == &a && d.active_tokens == 0);
    assert(b.busy == (scenario == 0));
    if (scenario == 0) b.CheckRelease();
    ++passed;
  }
  {
    // 三个候选中跳过满区、占用区，取得实际空区后仅封闭旧预留一次。
    ZonedBlockDevice d; Zone a, b, c, e; d.io_zones = {&a, &b, &c, &e};
    reserve(d); used(a, 256); used(b, 0); assert(c.Acquire());
    assert(take(d, 512) == &e);
    assert(a.finishes == 1 && !a.busy && a.capacity_ == 0);
    assert(!b.busy && c.busy && e.busy && d.migration_reserve_zone_ == &e);
    assert(d.active_tokens == 0 && d.ReleaseMigrateZone(&e).ok());
    c.CheckRelease(); ++passed;
  }
  std::cout << "迁移资源与 RR 源区边界测试 " << passed << " 项通过" << std::endl;
}
