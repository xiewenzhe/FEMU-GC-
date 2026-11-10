// Run the exact native allocator adapter with observable fake Zone/token backends.
#include "rrzc_caza_bridge.h"
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <iostream>

struct IOStatus {
  bool good = true;
  bool ok() const { return good; }
  static IOStatus OK() { return {}; }
  static IOStatus Corruption(const std::string&) { return {false}; }
  static IOStatus InvalidArgument(const std::string&) { return {false}; }
};
struct Env { enum WriteLifeTimeHint { WLTH_MEDIUM }; };
namespace rrzc_content {
inline bool Compatible(uint8_t resident, uint8_t incoming) { return resident == incoming; }
inline bool Claimable(uint8_t resident, uint8_t incoming) { return resident == 0 || resident == incoming; }
}
struct Tag {
  uint8_t value = 1;
  uint8_t load() const { return value; }
  void store(uint8_t next) { value = next; }
};
struct Zone {
  uint64_t start_ = 0, capacity_ = 4096;
  Env::WriteLifeTimeHint lifetime_ = Env::WLTH_MEDIUM;
  bool busy = false, empty = false, full = false;
  Tag rrzc_content_;
  struct Reset { bool known = true; uint64_t generation = 1; } reset;
  unsigned releases = 0;
  bool Acquire() { if (busy) return false; busy = true; return true; }
  Reset GetResetState() const { return reset; }
  bool IsEmpty() const { return empty; }
  bool IsFull() const { return full; }
  IOStatus CheckRelease() { assert(busy); busy = false; ++releases; return {}; }
};
static void Info(void*, const char*, ...) {}
class ZonedBlockDevice {
 public:
  void* logger_ = nullptr;
  std::map<uint64_t, Zone> zones;
  Zone* empty = nullptr;
  bool active_available = true, content_guard = true;
  IOStatus reset_status;
  int active_debt = 0;
  unsigned reset_calls = 0, empty_calls = 0;
  Zone* GetIOZone(uint64_t start) { return zones.count(start) ? &zones.at(start) : nullptr; }
  bool IsContentGuardEnabled() const { return content_guard; }
  IOStatus ResetUnusedIOZones() { ++reset_calls; return reset_status; }
  bool GetActiveIOZoneTokenIfAvailable() {
    if (!active_available) return false;
    ++active_debt; return true;
  }
  void PutActiveIOZoneToken() { --active_debt; assert(active_debt == 0); }
  IOStatus AllocateEmptyZone(Zone** out) {
    ++empty_calls; *out = empty;
    if (empty) assert(empty->Acquire());
    return {};
  }
  IOStatus TryCazaIOZone(Env::WriteLifeTimeHint, uint8_t, Zone**);
};
#include "zenfs_adapter.inc"

static ZonedBlockDevice Device() {
  ZonedBlockDevice d;
  for (uint64_t start : {100, 200, 300}) d.zones[start].start_ = start;
  return d;
}
int main() {
  assert(rrzc_caza::Enabled());
  rrzc_caza::Context context;
  context.request = {9, 0, "0010", "0020"};
  context.snapshot.files = {{1, 1, "0010", "0020", {{100, 1}, {200, 1}}},
                            {2, 1, "0010", "0020", {{100, 1}}},
                            {3, 0, "0021", "0025", {{300, 1}}}};
  rrzc_caza::Scope scope(context);
  Zone* target = nullptr;
  {
    auto d = Device(); d.zones[100].reset.generation = 2;
    assert(d.TryCazaIOZone(Env::WLTH_MEDIUM, 1, &target).ok());
    assert(target == &d.zones[200] && target->busy);
    assert(d.zones[100].releases == 1 && d.reset_calls == 0 && d.active_debt == 0);
  }
  {
    auto d = Device(); d.zones[100].busy = true; d.zones[200].full = true;
    Zone empty; empty.start_ = 400; empty.empty = true; empty.rrzc_content_.value = 0;
    d.empty = &empty;
    assert(d.TryCazaIOZone(Env::WLTH_MEDIUM, 1, &target).ok());
    assert(target == &empty && empty.busy && d.active_debt == 1);
    assert(empty.rrzc_content_.value == 1 && d.reset_calls == 1);
    assert(d.zones[100].busy && d.zones[100].releases == 0);  // Other owner's lock retained.
  }
  {
    auto d = Device(); d.zones[100].full = d.zones[200].full = true;
    assert(d.TryCazaIOZone(Env::WLTH_MEDIUM, 1, &target).ok());
    assert(target == &d.zones[300] && d.empty_calls == 1 && d.active_debt == 0);
  }
  {
    auto d = Device(); d.zones[100].full = d.zones[200].full = true;
    d.reset_status.good = false;
    assert(!d.TryCazaIOZone(Env::WLTH_MEDIUM, 1, &target).ok());
    assert(target == nullptr && d.empty_calls == 0 && d.active_debt == 0);
  }
  {
    auto d = Device(); d.zones[100].rrzc_content_.value = 2;
    assert(d.TryCazaIOZone(Env::WLTH_MEDIUM, 1, &target).ok());
    assert(target == &d.zones[200] && !d.zones[100].busy);
  }
  {
    auto d = Device(); d.zones[100].full = d.zones[200].full = true;
    Zone empty; empty.empty = true; empty.rrzc_content_.value = 2; d.empty = &empty;
    assert(!d.TryCazaIOZone(Env::WLTH_MEDIUM, 1, &target).ok());
    assert(target == nullptr && !empty.busy && d.active_debt == 0);
  }
  {
    auto d = Device(); d.active_available = false;
    for (auto& item : d.zones) item.second.full = true;
    assert(d.TryCazaIOZone(Env::WLTH_MEDIUM, 1, &target).ok());
    assert(target == nullptr && d.empty_calls == 0 && d.active_debt == 0);
  }
  std::cout << "CAZA generation/content/lock/active-token adapter tests passed; fake backend only\n";
}
