// 默认关闭的公共目标资格修复；不选择目标、不改变实际迁移分配器。
#pragma once
#include "rrzc_guarded_selector.h"

namespace rrzc_target {
inline bool Enabled(const char* value,bool greedy,bool reserved) {
  return value && !std::strcmp(value,"1") && greedy && reserved;
}
inline bool Fits(uint64_t source,uint64_t valid,bool covered,
                 const rrzc_guard::Bounds& bounds,uint64_t capacity,
                 const rrzc_cost::ReserveObservation& before,
                 const rrzc_cost::ReserveObservation& after) {
  // 覆盖必须完整，所有片段按有稀疏头的物理上界累加；未知时不补资格。
  if (!valid || !covered || !bounds.Complete(valid) ||
      !rrzc_guard::StableReserve(before,after) || source==after.zone) return false;
  if (!capacity || after.remaining>capacity || after.valid>capacity ||
      __uint128_t(after.remaining)+after.valid>capacity || after.wp<after.zone ||
      __uint128_t(after.wp-after.zone)+after.remaining!=capacity) return false;
  return bounds.upper<=after.remaining;
}
}  // 命名空间结束。
