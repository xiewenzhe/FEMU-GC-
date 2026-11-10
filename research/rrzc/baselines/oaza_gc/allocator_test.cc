// Execute the exact adapter with a token-observing Zone backend, not a device.
#include "rrzc_caza_bridge.h"
#include <cassert>
#include <iostream>
struct IOStatus {
  bool good = true;
  bool ok() const { return good; }
  static IOStatus OK() { return {}; }
  static IOStatus InvalidArgument(const std::string&) { return {false}; }
};
struct Env { enum WriteLifeTimeHint { WLTH_NOT_SET, WLTH_NONE, WLTH_SHORT, WLTH_MEDIUM, WLTH_LONG, WLTH_EXTREME }; };
namespace rrzc_content {
inline bool Compatible(uint8_t a, uint8_t b) { return a == b; }
inline bool Claimable(uint8_t a, uint8_t b) { return !a || a == b; }
}
struct Tag {
  uint8_t value = 1;
  uint8_t load() const { return value; }
  void store(uint8_t v) { value = v; }
};
struct Zone {
  uint64_t start_ = 0, capacity_ = 1000;
  Env::WriteLifeTimeHint lifetime_ = Env::WLTH_LONG;
  bool busy = false, empty = false;
  Tag rrzc_content_;
  struct Reset { bool known = true; uint64_t generation = 1; } reset;
  bool Acquire() { if (busy) return false; busy = true; return true; }
  Reset GetResetState() const { return reset; }
  bool IsEmpty() const { return empty; }
  IOStatus CheckRelease() { assert(busy); busy = false; return {}; }
};
static void Info(void*, const char*, ...) {}
class ZonedBlockDevice {
 public:
  void* logger_ = nullptr;
  std::vector<Zone*> io_zones;
  Zone* empty = nullptr;
  int debt = 0;
  bool active = true;
  Zone* GetIOZone(uint64_t start) { for (auto* z : io_zones) if (z->start_ == start) return z; return nullptr; }
  bool IsContentGuardEnabled() const { return true; }
  uint64_t GetZoneSize() const { return 2000; }
  IOStatus ResetUnusedIOZones() { return {}; }
  bool GetActiveIOZoneTokenIfAvailable() { if (!active) return false; ++debt; return true; }
  void PutActiveIOZoneToken() { --debt; assert(debt == 0); }
  IOStatus AllocateEmptyZone(Zone** out) { *out = empty; if (empty) assert(empty->Acquire()); return {}; }
  IOStatus TryCazaIOZone(Env::WriteLifeTimeHint, uint8_t, Zone**);
};
#include "allocator.inc"
static rrzc_caza::File F(uint64_t id, int level, const char* low, const char* high, uint64_t keys, uint64_t zone) {
  rrzc_caza::File f; f.number = id; f.level = level; f.smallest = low; f.largest = high; f.keys = keys;
  f.zones = {{zone, 1}}; f.bytes = 100; return f;
}
int main() {
  assert(rrzc_caza::Enabled());
  rrzc_caza::Context c;
  c.snapshot.cold_options.levels = 5; c.snapshot.cold_options.base_level = 1;
  c.request.number = 90; c.request.level = 2; c.request.smallest = "0010"; c.request.largest = "0020";
  c.request.keys = 10; c.request.bytes = 500;
  c.snapshot.files = {F(1, 3, "0000", "0015", 30, 900), F(2, 2, "0010", "0020", 10, 100),
                      F(3, 2, "0010", "0020", 20, 200)};
  rrzc_caza::Scope scope(c);
  Zone first, second; first.start_ = 100; second.start_ = 200;
  first.capacity_ = 499;
  ZonedBlockDevice d; d.io_zones = {&first, &second};
  Zone* target = nullptr;
  assert(d.TryCazaIOZone(Env::WLTH_LONG, 1, &target).ok() && target == &second);
  assert(!first.busy && second.busy);  // Whole-file fit rejected nearest ratio, tried next ratio.
  second.CheckRelease(); second.reset.generation = 2;
  assert(d.TryCazaIOZone(Env::WLTH_LONG, 1, &target).ok() && !target);
  assert(!first.busy && !second.busy && !d.debt);  // Unavailable -> lifetime allocator fallback.
  c.snapshot.files.clear();
  Zone fresh; fresh.empty = true; fresh.rrzc_content_.value = 0; fresh.start_ = 300;
  d.empty = &fresh;
  assert(d.TryCazaIOZone(Env::WLTH_LONG, 1, &target).ok() && target == &fresh && d.debt == 1);
  assert(rrzc_caza::Bridge().Association(90, 2) == 3 && fresh.lifetime_ == Env::WLTH_EXTREME);
  fresh.CheckRelease(); d.PutActiveIOZoneToken();
  d.active = false; c.request.number = 91;
  assert(d.TryCazaIOZone(Env::WLTH_LONG, 1, &target).ok() && !target);
  assert(rrzc_caza::Bridge().Association(91, 2) == 2 && !d.debt);
  c.request.level = 0; first.capacity_ = 1000; first.lifetime_ = Env::WLTH_MEDIUM;
  assert(d.TryCazaIOZone(Env::WLTH_MEDIUM, 1, &target).ok() && target == &first);
  std::cout << "OAZA ratio order/whole-file fit/generation/fresh promotion/token failure/top lifetime passed\n";
}
