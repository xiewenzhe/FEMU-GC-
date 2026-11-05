// 独立候选范围实验：只解除无效比例过滤，不改变触发、排序或迁移安全检查。
#pragma once
#include "rrzc_exact_live.h"

namespace rrzc_pool {
struct Config { bool valid=true,expanded=false; };
inline Config Parse(const char* value,bool direct) {
  if (!value || !std::strcmp(value,"original")) return {};
  if (!std::strcmp(value,"expanded") && direct) return {true,true};
  return {false,false};
}
// 调用方仍只遍历满区；有效量、覆盖、代际与目标容量由原公共安全路径复核。
inline bool Selected(uint64_t valid,uint64_t capacity,uint64_t garbage,
                     uint64_t threshold,bool exact,bool expanded) {
  if (!expanded) return rrzc_exact::Selected(valid,capacity,garbage,threshold,exact);
  return capacity && valid>0 && valid<capacity;
}
}  // 候选范围实验命名空间结束。
