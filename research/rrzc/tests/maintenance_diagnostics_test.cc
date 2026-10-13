// 后端与日志为内存替身，执行从生产源码提取的 Reset 和诊断函数。
// 不模拟真实设备、SST 持久化或性能；人工注入失败仅为测试数据。
#include <atomic>
#include <cassert>
#include <cstdio>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <vector>

struct IOStatus {
  bool success = true;
  bool ok() const { return success; }
  static IOStatus OK() { return {}; }
  bool operator!=(const IOStatus& other) const { return success != other.success; }
  std::string ToString() const { return success ? "成功" : "人工注入的后端失败"; }
};
struct Logger { std::vector<std::string> lines; };
template <typename... T>
void Info(const std::shared_ptr<Logger>& logger, const char* format, T... values) {
  char buffer[2048];
  std::snprintf(buffer, sizeof(buffer), format, values...);
  logger->lines.emplace_back(buffer);
}
struct Env { enum WriteLifeTimeHint { WLTH_NOT_SET, WLTH_MEDIUM }; };
struct ZoneResetState {
  uint64_t start = 0, generation = 0, sequence = 0;
  bool stable = false, known = false;
};
struct Backend {
  bool fail = false, offline = false;
  std::function<void()> during_reset;
  IOStatus Reset(uint64_t, bool* out_offline, uint64_t* max_capacity) {
    if (during_reset) during_reset();
    if (fail) return {false};
    *out_offline = offline; *max_capacity = 4096; return {};
  }
};
struct ZonedBlockDevice;
struct Zone {
  ZonedBlockDevice* zbd_ = nullptr;
  Backend* zbd_be_ = nullptr;
  std::atomic<uint64_t> reset_generation_{0}, reset_sequence_{0};
  std::atomic<bool> reset_generation_known_{true};
  uint64_t start_ = 8192, wp_ = 9216, capacity_ = 3072, max_capacity_ = 4096;
  std::atomic<uint64_t> used_capacity_{0};
  Env::WriteLifeTimeHint lifetime_ = Env::WLTH_MEDIUM;
  bool IsUsed() { return used_capacity_ != 0; }
  bool IsBusy() { return true; }
  ZoneResetState GetResetState() const;
  IOStatus Reset();
};
struct ZonedBlockDevice {
  bool maintenance_diag_enabled_ = false;
  std::shared_ptr<Logger> logger_ = std::make_shared<Logger>();
  std::vector<Zone*> io_zones, meta_zones;
  bool IsMaintenanceDiagnosticsEnabled() const { return maintenance_diag_enabled_; }
  Zone* GetIOZone(uint64_t start) {
    for (auto* z : io_zones) if (z->start_ == start) return z;
    return nullptr;
  }
  std::map<uint64_t, ZoneResetState> GetZoneResetStates() const;
  void LogZoneReset(const ZoneResetState&, uint64_t, uint64_t, const IOStatus&, bool);
};
struct ZoneSnapshot {
  uint64_t start = 8192, wp = 9216, capacity = 3072, used_capacity = 100;
};
struct ZoneReadPressure { uint32_t blocks_at_threshold = 1; };
#include "diagnostics-under-test.inc"

static bool has(const std::shared_ptr<Logger>& logger, const std::string& text) {
  return !logger->lines.empty() && logger->lines.back().find(text) != std::string::npos;
}
int main() {
  unsigned passed = 0;
  ZonedBlockDevice d; Backend backend; Zone zone;
  d.io_zones = {&zone}; zone.zbd_ = &d; zone.zbd_be_ = &backend;
  auto initial = zone.GetResetState();
  assert(initial.stable && initial.known && initial.generation == 0 && initial.sequence == 0); ++passed;
  assert(zone.Reset().ok() && zone.GetResetState().generation == 1);
  assert(zone.GetResetState().sequence == 2 && d.logger_->lines.empty()); ++passed;
  d.maintenance_diag_enabled_ = true;
  assert(zone.Reset().ok() && has(d.logger_, "状态=成功，主机代际=1/2")); ++passed;
  zone.wp_ = 10240; zone.capacity_ = 2048; backend.fail = true;
  assert(!zone.Reset().ok() && zone.wp_ == 10240 && zone.capacity_ == 2048);
  assert(zone.GetResetState().generation == 2 && !zone.GetResetState().known &&
         zone.GetResetState().sequence == 6 && has(d.logger_, "状态=失败")); ++passed;
  backend.fail = false;
  assert(zone.Reset().ok() && zone.GetResetState().known && zone.GetResetState().generation == 3); ++passed;
  backend.during_reset = [&] { assert(!zone.GetResetState().stable && zone.GetResetState().sequence % 2 == 1); };
  assert(zone.Reset().ok()); backend.during_reset = {}; ++passed;
  auto before = zone.GetResetState();
  assert(IsMaintenanceWindowStable(before, before, before)); ++passed;
  assert(zone.Reset().ok()); auto after = zone.GetResetState();
  assert(!IsMaintenanceWindowStable(before, after, after)); ++passed;
  backend.fail = true; before = zone.GetResetState(); assert(!zone.Reset().ok());
  after = zone.GetResetState(); assert(!IsMaintenanceWindowStable(before, after, after)); ++passed;
  backend.fail = false; assert(zone.Reset().ok()); before = zone.GetResetState();
  assert(zone.Reset().ok()); assert(!IsMaintenanceWindowStable(before, before, zone.GetResetState())); ++passed;
  auto states = d.GetZoneResetStates(); ZoneSnapshot snapshot; ZoneReadPressure pressure;
  auto observe = [&](uint64_t covered, bool non_sst, const ZoneReadPressure* p = nullptr) {
    LogMaintenanceObservation(d.logger_, &d, "测试", snapshot, p, covered, non_sst, states, states);
  };
  observe(100, false, &pressure); assert(has(d.logger_, "SST覆盖资格=1")); ++passed;
  observe(100, true); assert(has(d.logger_, "含非SST=1，覆盖完整=1，SST覆盖资格=0")); ++passed;
  observe(99, false); assert(has(d.logger_, "含非SST=0，覆盖完整=0，SST覆盖资格=0")); ++passed;
  observe(100, false); assert(has(d.logger_, "读压力可用=0，达阈值块=0")); ++passed;
  snapshot.used_capacity = 0; snapshot.wp = snapshot.start;
  observe(0, false); assert(has(d.logger_, "SST覆盖资格=0")); ++passed;
  d.maintenance_diag_enabled_ = false; auto count = d.logger_->lines.size();
  observe(0, false); assert(d.logger_->lines.size() == count); ++passed;
  Zone meta; meta.start_ = 0; d.meta_zones = {&meta};
  assert(d.GetZoneResetStates().size() == 2); ++passed;
  d.maintenance_diag_enabled_ = true; backend.offline = true;
  assert(zone.Reset().ok() && zone.capacity_ == 0 && has(d.logger_, "离线=1")); ++passed;
  std::cout << "维护诊断单元测试通过：" << passed << " 项；生产函数未修改选区资格" << std::endl;
}
