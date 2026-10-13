// 使用生产 AllocateIOZone() 函数，替换设备与扫描依赖，仅验证控制流和资源计数。
// 此测试不模拟 NAND、并发调度或真实 Zone 写入，不能替代客机集成测试。
#include <atomic>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>

struct IOStatus {
  bool success = true;
  bool ok() const { return success; }
  static IOStatus OK() { return {}; }
};
struct Env {
  enum WriteLifeTimeHint { WLTH_MEDIUM = 3 };
  static void* Default() { return nullptr; }
};
enum class IOType { kWAL, kUnknown };
enum { ZENFS_WAL_IO_ALLOC_LATENCY, ZENFS_L0_IO_ALLOC_LATENCY,
       ZENFS_NON_WAL_IO_ALLOC_LATENCY, ZENFS_IO_ALLOC_QPS,
       ZENFS_OPEN_ZONES_COUNT, ZENFS_ACTIVE_ZONES_COUNT };
constexpr unsigned LIFETIME_DIFF_NOT_GOOD = 100;
constexpr unsigned LIFETIME_DIFF_COULD_BE_WORSE = 50;
struct Metrics {
  void ReportQPS(int, int) {}
  void ReportGeneral(int, long) {}
};
struct ZenFSMetricsLatencyGuard {
  ZenFSMetricsLatencyGuard(std::shared_ptr<Metrics>, int, void*) {}
};
template <typename... T> void Info(void*, const char*, T...) {}
template <typename... T> void Debug(void*, const char*, T...) {}
unsigned wait_calls = 0;
// 控制流测试使用虚拟等待计数，不真的等待 10 秒。
int usleep(unsigned) { ++wait_calls; return 0; }
struct Zone {
  bool busy = false, empty = false;
  uint64_t start_ = 256, wp_ = 512, capacity_ = 4096;
  Env::WriteLifeTimeHint lifetime_ = Env::WLTH_MEDIUM;
  bool IsBusy() { return busy; }
  bool IsEmpty() { return empty; }
  IOStatus CheckRelease() { assert(busy); busy = false; return {}; }
};
struct ZonedBlockDevice {
  Zone partial, empty;
  bool allocation_diag_enabled_ = true, allocation_fallback_enabled_ = false;
  bool have_empty = false, lose_match = false, rescan_error = false;
  bool temporary_busy = false, empty_after_wait = false, empty_retry_error = false;
  unsigned diff = 50, scans = 0, empty_scans = 0;
  std::atomic<long> active_io_zones_{2}, open_io_zones_{0};
  unsigned max_nr_active_io_zones_ = 4, max_nr_open_io_zones_ = 32;
  void* logger_ = nullptr;
  std::shared_ptr<Metrics> metrics_ = std::make_shared<Metrics>();
  IOStatus GetZoneDeferredStatus() { return {}; }
  IOStatus ApplyFinishThreshold() { return {}; }
  void WaitForOpenIOZoneToken(bool) { ++open_io_zones_; }
  void PutOpenIOZoneToken() { assert(open_io_zones_ > 0); --open_io_zones_; }
  bool GetActiveIOZoneTokenIfAvailable() {
    if (active_io_zones_ >= max_nr_active_io_zones_) return false;
    ++active_io_zones_; return true;
  }
  void PutActiveIOZoneToken() { assert(active_io_zones_ > 0); --active_io_zones_; }
  IOStatus FinishCheapestIOZone() { assert(false); return {false}; }
  IOStatus GetBestOpenZoneMatch(Env::WriteLifeTimeHint, unsigned* best,
                               Zone** out, uint32_t = 0, std::string* = nullptr) {
    ++scans; *out = nullptr; *best = diff;
    if (scans > 1 && rescan_error) return {false};
    if (scans > 1 && lose_match) return {};
    if (scans == 2 && temporary_busy) return {};
    assert(!partial.busy); partial.busy = true; *out = &partial; return {};
  }
  IOStatus AllocateEmptyZone(Zone** out, std::string* = nullptr) {
    ++empty_scans;
    *out = nullptr;
    if (empty_scans > 1 && empty_retry_error) return {false};
    if (have_empty || (empty_after_wait && wait_calls > 0)) {
      empty.busy = empty.empty = true; *out = &empty;
    }
    return {};
  }
  void LogZoneStats() {}
  IOStatus AllocateIOZone(Env::WriteLifeTimeHint, IOType, Zone**);
};

// 构建脚本从当前生产源文件机械提取函数，避免测试复制一份过期实现。
#include "allocator-under-test.inc"

int main() {
  for (int scenario = 0; scenario < 11; ++scenario) {
    wait_calls = 0;
    ZonedBlockDevice z;
    z.allocation_fallback_enabled_ = scenario != 0;
    z.have_empty = scenario == 2;
    z.diff = scenario == 3 ? 100 : 50;
    z.rescan_error = scenario == 4;
    z.lose_match = scenario == 5;
    z.temporary_busy = scenario == 7 || scenario == 8;
    z.empty_after_wait = scenario == 8;
    z.empty_retry_error = scenario == 10;
    if (scenario == 9) z.active_io_zones_ = 4;
    Zone* out = nullptr;
    auto s = z.AllocateIOZone(Env::WLTH_MEDIUM,
                             scenario == 6 ? IOType::kWAL : IOType::kUnknown,
                             &out);
    assert(s.ok() == (scenario != 4 && scenario != 10));
    if (scenario == 1 || scenario == 2 || scenario == 7 || scenario == 8 ||
        scenario == 9) {
      const bool new_empty = scenario == 2 || scenario == 8;
      assert(out == (new_empty ? &z.empty : &z.partial));
      assert(out->busy && z.open_io_zones_ == 1);
      assert(z.active_io_zones_ == (scenario == 9 ? 4 : (new_empty ? 3 : 2)));
      assert(z.scans == (scenario == 7 ? 3u :
                        ((scenario == 2 || scenario == 9) ? 1u : 2u)));
      out->CheckRelease(); z.PutOpenIOZoneToken();
      if (new_empty) z.PutActiveIOZoneToken();
    } else {
      assert(out == nullptr && z.open_io_zones_ == 0);
      assert(z.active_io_zones_ == 2 && !z.partial.busy);
      assert(z.scans == ((scenario == 0 || scenario == 6 || scenario == 10)
                            ? 1u : (scenario == 4 ? 2u : 201u)));
    }
    assert(wait_calls == ((scenario == 3 || scenario == 5) ? 199u :
                          ((scenario == 7 || scenario == 8) ? 1u : 0u)));
    std::cout << "分配边界测试 " << scenario + 1 << " 通过" << std::endl;
  }
}
