// 只读后台采样；不执行维护，不放宽同块身份、代际或时间窗口检查。
#pragma once
#include "rrzc_v1_growth.h"
#include <chrono>
#include <condition_variable>
#include <functional>
#include <mutex>
#include <thread>

namespace rrzc_v1 {
inline bool IndependentSamplingEnabled(const char* value, bool guarded,
                                       bool cost_observation) {
  return value && std::strcmp(value,"1")==0 && guarded && !cost_observation;
}
class RecentSampler {
 public:
  using Query=std::function<bool(RecentReport*)>;
  struct Snapshot { GrowthHistory history; uint64_t sequence=0; };
  explicit RecentSampler(Query query,
      std::chrono::nanoseconds period=std::chrono::seconds(1))
      : query_(std::move(query)),period_(period) {}
  ~RecentSampler() { Stop(); }
  RecentSampler(const RecentSampler&)=delete;
  RecentSampler& operator=(const RecentSampler&)=delete;
  void Start() { worker_=std::thread(&RecentSampler::Run,this); }
  Snapshot Freeze() {
    std::lock_guard<std::mutex> lock(mutex_);
    return {history_,sequence_};
  }
  void Clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    history_.Clear(); ++sequence_;
  }
  void Stop() {
    { std::lock_guard<std::mutex> lock(mutex_); stop_=true; }
    wake_.notify_all();
    // 查询使用局部缓冲；必须等待查询及线程退出，之后才能销毁设备。
    if (worker_.joinable()) worker_.join();
  }
 private:
  void Run() {
    auto due=std::chrono::steady_clock::now()+period_;
    std::unique_lock<std::mutex> lock(mutex_);
    while (!wake_.wait_until(lock,due,[this]{return stop_;})) {
      lock.unlock();
      RecentReport sample;
      bool good=false;
      try { good=query_(&sample); } catch (...) { good=false; }
      lock.lock();
      if (good) history_.Push(sample); else history_.Clear();
      ++sequence_;
      // 慢查询跨过的采样点直接跳过；不持锁查询，也不密集补采。
      do { due+=period_; } while (due<=std::chrono::steady_clock::now());
    }
  }
  Query query_;
  std::chrono::nanoseconds period_;
  std::mutex mutex_;
  std::condition_variable wake_;
  GrowthHistory history_;
  uint64_t sequence_=0;
  bool stop_=false;
  std::thread worker_;
};
}  // 采样命名空间。
