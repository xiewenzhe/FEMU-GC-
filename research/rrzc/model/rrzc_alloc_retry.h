// 调用者已经独占真实空目标；补全活跃令牌竞争失败后的原Finish重试。
#pragma once
#include <cstdint>
namespace rrzc_alloc {
constexpr unsigned kWaitAttempts=1200;
constexpr uint64_t Headroom(uint64_t capacity) { return (capacity+1)/2; }
constexpr bool Enough(uint64_t remaining,uint64_t capacity) {
  return capacity && remaining>=Headroom(capacity);
}
template<class Status,class Acquire,class Finish>
Status ActivateClaimedEmpty(Acquire acquire,Finish finish,bool* active) {
  *active=acquire();
  if (*active) return Status::OK();
  auto status=finish();
  if (!status.ok()) return status;
  *active=acquire();
  return status;
}
}  // 命名空间结束。
