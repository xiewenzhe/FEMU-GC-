// 只记录本进程迁移复制及映射提交的活动窗口，不参与RR／ZC准入或选区。
#pragma once
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <functional>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <utility>
#include <vector>

namespace rrzc_observe {
inline bool MigrationWindowEnabled(const char* value) {
  return value && std::strcmp(value, "1") == 0;
}

class MigrationWindow {
 public:
  using Clock = std::function<uint64_t()>;
  struct ZoneState {
    uint64_t sequence = 0, active = 0, last_end_ns = 0;
  };
  struct Frame {
    bool enabled = false, reliable = false;
    uint64_t owner = 0, created_ns = 0, captured_ns = 0;
    std::map<uint64_t, ZoneState> zones;
    ZoneState Get(uint64_t zone) const {
      const auto it = zones.find(zone);
      return it == zones.end() ? ZoneState{} : it->second;
    }
  };
  struct Evidence {
    bool known = false, clean = false;
    const char* reason = "迁移历史未知";
  };
  class Scope {
   public:
    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;
    ~Scope() { owner_->End(zones_); }
   private:
    friend class MigrationWindow;
    Scope(MigrationWindow* owner, std::vector<uint64_t> zones)
        : owner_(owner), zones_(std::move(zones)) {}
    MigrationWindow* owner_;
    std::vector<uint64_t> zones_;
  };

  explicit MigrationWindow(bool enabled, Clock clock = [] {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
  }) : enabled_(enabled), clock_(std::move(clock)) {
    if (enabled_) {
      static std::atomic<uint64_t> next{1};
      owner_ = next.fetch_add(1);
      created_ns_ = last_time_ns_ = clock_();
      if (!owner_) reliable_ = false;
    }
  }

  bool Enabled() const { return enabled_; }

  std::unique_ptr<Scope> Mark(const std::vector<uint64_t>& zones) {
    if (!enabled_ || zones.empty()) return nullptr;
    // 每次只持有统计锁，不在锁内调用文件、设备或日志接口。
    auto scope = std::unique_ptr<Scope>(new Scope(this, zones));
    std::lock_guard<std::mutex> lock(mutex_);
    Now();
    for (uint64_t zone : zones) {
      auto& state = zones_[zone];
      Increment(state.sequence); Increment(state.active);
    }
    return scope;
  }

  Frame Freeze() {
    if (!enabled_) return {};
    std::lock_guard<std::mutex> lock(mutex_);
    Frame frame;
    frame.captured_ns = Now();
    frame.enabled = true; frame.reliable = reliable_; frame.owner = owner_;
    frame.created_ns = created_ns_; frame.zones = zones_;
    return frame;
  }

  static Evidence Check(const Frame& before, const Frame& after, uint64_t zone,
                        uint64_t begin_ns, uint64_t end_ns) {
    if (!before.enabled || !after.enabled) return {false, false, "迁移统计未启用"};
    if (!before.reliable || !after.reliable || !before.owner || before.owner != after.owner ||
        before.created_ns != after.created_ns)
      return {false, false, "统计损坏或统计实例改变"};
    if (begin_ns > end_ns || before.created_ns > begin_ns ||
        before.captured_ns < before.created_ns || before.captured_ns > begin_ns ||
        after.captured_ns < end_ns || after.captured_ns < before.captured_ns)
      return {false, false, "历史不足或快照未包围观察窗口"};
    const auto a = before.Get(zone), b = after.Get(zone);
    if (b.sequence < a.sequence || b.last_end_ns < a.last_end_ns ||
        a.last_end_ns > before.captured_ns || b.last_end_ns > after.captured_ns)
      return {false, false, "迁移序号或时间无效"};
    if (a.active || b.active || a.sequence != b.sequence ||
        (b.sequence && b.last_end_ns >= begin_ns))
      return {true, false, "观察窗口含迁移活动或完成边界"};
    return {true, true, "历史窗口内无已观测迁移触及"};
  }

 private:
  uint64_t Now() {
    const uint64_t now = clock_();
    if (now < last_time_ns_) reliable_ = false;
    last_time_ns_ = now;
    return now;
  }
  void Increment(uint64_t& value) {
    if (value == std::numeric_limits<uint64_t>::max()) reliable_ = false;
    else ++value;
  }
  void End(const std::vector<uint64_t>& zones) {
    std::lock_guard<std::mutex> lock(mutex_);
    const uint64_t now = Now();
    for (uint64_t zone : zones) {
      auto& state = zones_[zone];
      if (!state.active) reliable_ = false;
      else --state.active;
      Increment(state.sequence); state.last_end_ns = now;
    }
  }
  const bool enabled_;
  Clock clock_;
  std::mutex mutex_;
  bool reliable_ = true;
  uint64_t owner_ = 0, created_ns_ = 0, last_time_ns_ = 0;
  std::map<uint64_t, ZoneState> zones_;
};
}  // 只读观测命名空间。
