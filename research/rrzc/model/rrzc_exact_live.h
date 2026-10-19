// 原无效比例门槛不变；显式修复小于百分之一有效量被整数舍入误判为空。
#pragma once
#include <cstdint>
#include <cstring>
namespace rrzc_exact {
inline bool Enabled(const char* value,bool recovery) {
  return recovery && value && !std::strcmp(value,"1");
}
inline bool Selected(uint64_t valid,uint64_t capacity,uint64_t garbage,
                     uint64_t threshold,bool exact) {
  if (!capacity || valid>capacity) return false;
  return garbage>threshold && (exact ? valid>0 : garbage<100);
}
}  // 命名空间结束。
