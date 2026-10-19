// 默认关闭的公共空间紧急恢复；不使用读风险，不调整正常候选及评分。
#pragma once
#include "rrzc_weighted_selector.h"
#include <atomic>
#include <cstring>

namespace rrzc_space {
inline bool Enabled(const char* value,bool recovery) {
  return recovery && value && !std::strcmp(value,"1");
}
class Pressure {
  std::atomic<uint64_t> waiters_{0};
 public:
  uint64_t Count() const { return waiters_.load(); }
  class Wait {
    Pressure& owner_;
   public:
    explicit Wait(Pressure& owner):owner_(owner) { ++owner_.waiters_; }
    ~Wait() { --owner_.waiters_; }
    Wait(const Wait&)=delete;
    Wait& operator=(const Wait&)=delete;
  };
};
inline rrzc_simple::Decision Choose(const std::vector<rrzc_simple::Candidate>& candidates,
    bool enabled,uint64_t waiters,bool normal_found) {
  if (!enabled || !waiters || normal_found) return {};
  // 调用者只提交满区、同代际、完整SST且专用预留物理上界足够的候选。
  // 正回收仍是必要条件；同有效量按Zone编号打破平局。
  return rrzc_simple::Choose(candidates,1,false);
}
}  // 命名空间结束。
