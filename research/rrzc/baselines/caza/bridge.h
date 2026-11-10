// Shared by the independently rebuilt RocksDB and ZenFS translation units.
// No calls back into RocksDB/ZenFS, no I/O, and no locks held across allocation.
#pragma once
#include "rrzc_caza_policy.h"
#include <cstdlib>
#include <memory>
#include <mutex>
#include <limits>

namespace rrzc_caza {
inline bool Enabled() {
  static const bool enabled = [] {
    const char* mode = std::getenv("ZENFS_RELATED_WORK");
    if (!mode) return false;
    if (std::string(mode) != "caza") throw std::invalid_argument("unknown related-work mode");
    // Reproduction must not quietly inherit the original cooperative policy.
    for (const char* key : {"ZENFS_RRZC_PLACEMENT_MODE", "ZENFS_RRZC_POOL_K",
          "ZENFS_RRZC_STABLE_CREDIT", "ZENFS_RRZC_RECENT_ETA", "ZENFS_RRZC_UNIT_COST",
          "ZENFS_RRZC_RECLAIM_PRICE", "ZENFS_RRZC_SPACE_GOAL"})
      if (std::getenv(key)) throw std::invalid_argument(std::string("CAZA conflicts with ") + key);
    return true;
  }();
  return enabled;
}

inline std::string Path(const std::string& path) {
  std::string result = path;
  while (result.size() > 1 && result.back() == '/') result.pop_back();
  return result;
}
inline std::string Parent(const std::string& path) {
  const auto slash = path.find_last_of('/');
  return slash == std::string::npos ? "." : slash == 0 ? "/" : path.substr(0, slash);
}
inline bool IsSst(const std::string& path) {
  return path.size() >= 4 && path.compare(path.size() - 4, 4, ".sst") == 0;
}
inline uint64_t Number(const std::string& path) {
  const auto begin = path.find_last_of('/') + 1;
  const auto suffix = path.size() - 4;
  if (!IsSst(path) || begin >= suffix) throw std::invalid_argument("invalid SST path");
  uint64_t value = 0;
  for (size_t i = begin; i < suffix; ++i) {
    if (path[i] < '0' || path[i] > '9' ||
        value > (std::numeric_limits<uint64_t>::max() - (path[i] - '0')) / 10)
      throw std::invalid_argument("invalid SST number");
    value = value * 10 + (path[i] - '0');
  }
  return value;
}

struct Live { std::string path, cf; File file; };
struct Pending { bool ready = false; Request request; };
struct Snapshot { uint64_t version = 0; std::vector<File> files; };
class Registry {
 public:
  std::shared_ptr<Pending> Register(const std::string& name) {
    const auto path = Path(name);
    std::lock_guard<std::mutex> lock(mutex_);
    if (pending_.count(path)) throw std::invalid_argument("SST already open for CAZA");
    auto item = std::make_shared<Pending>();
    item->request.number = Number(path);
    pending_[path] = item;
    return item;
  }
  void Unregister(const std::string& name, const std::shared_ptr<Pending>& item) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto found = pending_.find(Path(name));
    if (found != pending_.end() && found->second == item) pending_.erase(found);
  }
  void Ready(const std::string& name, int level, const std::string& low, const std::string& high) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto found = pending_.find(Path(name));
    if (found == pending_.end()) return;  // POSIX and other filesystem backends unchanged.
    Request request{found->second->request.number, level, low, high};
    Validate(request, {});
    if (found->second->ready) throw std::invalid_argument("SST metadata published twice");
    found->second->request = std::move(request);
    found->second->ready = true;
  }
  bool Read(const std::shared_ptr<Pending>& item, Request* request) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!item->ready) return false;
    *request = item->request;
    return true;
  }
  void Publish(const std::string& db, std::vector<Live> files) {
    std::lock_guard<std::mutex> lock(mutex_);
    live_[Path(db)] = std::move(files);
    ++version_;
  }
  void Bind(uint64_t device, const std::string& name, std::vector<ZoneId> zones) {
    std::lock_guard<std::mutex> lock(mutex_);
    bindings_[{device, Path(name)}] = std::move(zones);
    ++version_;
  }
  Snapshot Capture(uint64_t device, const std::string& name) {
    std::lock_guard<std::mutex> lock(mutex_);
    Snapshot result;
    result.version = version_;
    const auto found = live_.find(Parent(Path(name)));
    if (found == live_.end()) throw std::invalid_argument("CAZA has no committed LSM snapshot");
    for (const auto& live : found->second) {
      if (live.cf != "default") throw std::invalid_argument("CAZA currently supports default CF only");
      const auto physical = bindings_.find({device, Path(live.path)});
      if (physical == bindings_.end()) throw std::invalid_argument("live SST has no confirmed Zone mapping");
      auto file = live.file;
      file.zones = physical->second;
      result.files.push_back(std::move(file));
    }
    return result;
  }
 private:
  std::mutex mutex_;
  uint64_t version_ = 0;
  std::map<std::string, std::shared_ptr<Pending>> pending_;
  std::map<std::string, std::vector<Live>> live_;
  std::map<std::pair<uint64_t, std::string>, std::vector<ZoneId>> bindings_;
};
inline Registry& Bridge() { static Registry registry; return registry; }

// The scope is only around the replay of an SST's buffered bytes. Nested scopes
// restore the previous request. No background thread borrows another file's state.
struct Context { Request request; Snapshot snapshot; };
inline thread_local const Context* current = nullptr;
class Scope {
 public:
  explicit Scope(const Context& context) : previous_(current) { current = &context; }
  ~Scope() { current = previous_; }
  Scope(const Scope&) = delete;
 private:
  const Context* previous_;
};
}  // namespace rrzc_caza
