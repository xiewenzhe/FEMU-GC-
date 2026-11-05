// 仅受控重复实验使用的空间交接；真实分配重试失败后请求一次原维护检查。
#pragma once
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>

namespace rrzc_boundary {
class Space {
 public:
  uint64_t Request() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (requested_!=completed_) return 0;
    return ++requested_;
  }
  uint64_t Pending() {
    std::lock_guard<std::mutex> lock(mutex_);
    return requested_>completed_?requested_:0;
  }
  bool Complete(uint64_t sequence) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!sequence || sequence!=completed_+1 || sequence!=requested_) return false;
    completed_=sequence;changed_.notify_all();return true;
  }
  bool Wait(uint64_t sequence,std::chrono::steady_clock::time_point deadline) {
    std::unique_lock<std::mutex> lock(mutex_);
    return sequence && changed_.wait_until(lock,deadline,[&]{return completed_>=sequence;});
  }
 private:
  std::mutex mutex_;std::condition_variable changed_;
  uint64_t requested_=0,completed_=0;
};
inline Space& State(){static Space state;return state;}
}  // 请求序号只用于交接，不预设维护次数、victim或目标。
