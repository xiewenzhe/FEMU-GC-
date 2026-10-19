// 仅负载知道的固定退役安排；不向选择器提供剩余寿命。
#pragma once
#include "competition_workload_plan.h"
#include <cstring>
namespace rrzc_lifetime {
constexpr unsigned StopAge=67;
inline unsigned RetireAge=68;
// 仅允许事先固定的两个条件；默认安排与首轮完全相同。
inline bool ConfigureRetireDelay(const char* value) {
  RetireAge=68;
  if(!value || !std::strcmp(value,"0")) return true;
  if(!std::strcmp(value,"20")) { RetireAge=88; return true; }
  return false;
}
constexpr bool Early(unsigned generation,unsigned kind) {
  return (generation%3==0 && kind==0) || (generation%3==2 && kind>0);
}
constexpr bool KeepRead(unsigned generation,unsigned kind,uint64_t age_ns) {
  return !Early(generation,kind) || age_ns<uint64_t(StopAge)*1000000000ULL;
}
}  // 命名空间结束。
