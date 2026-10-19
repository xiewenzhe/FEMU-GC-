// 默认关闭的公共单区恢复；不使用读风险，不改正常评分和空间触发条件。
#pragma once
#include "rrzc_weighted_selector.h"
#include <cstring>

namespace rrzc_recovery {
inline bool Enabled(const char* value,bool greedy,bool reserved) {
  return value && !std::strcmp(value,"1") && greedy && reserved;
}
struct Fallback { bool needed=false; rrzc_simple::Decision decision; };
inline Fallback Choose(const std::vector<rrzc_simple::Candidate>& candidates,uint64_t budget) {
  // 只有真实可迁移的正常候选才阻止应急；非SST或覆盖/目标未知区
  // 不能仅因数值预算满足，就阻塞其他覆盖完整且有安全目标的区。
  if (!budget || rrzc_simple::Choose(candidates,budget,false).found) return {};
  // 候选已由调用者通过原ZenFS空间门槛；这里只放宽额外实验预算至正回收。
  return {true,rrzc_simple::Choose(candidates,1,false)};
}
constexpr unsigned WaitAttempts(bool enabled) { return enabled ? 600 : 200; }
template<class Extent>
bool DiscardUncommitted(const std::vector<Extent*>& original,std::vector<Extent*>* copied) {
  // 调用者持有文件写锁且尚未发布新映射；源引用仍由原文件对象维护。
  if (original.size()!=copied->size()) return false;
  for (size_t i=0;i<original.size();++i)
    if (!original[i] || !(*copied)[i] || !original[i]->zone_ || !(*copied)[i]->zone_ ||
        original[i]->length_!=(*copied)[i]->length_) return false;
  for (size_t i=0;i<original.size();++i) {
    auto* ext=(*copied)[i];
    if (original[i]->start_!=ext->start_ || original[i]->zone_!=ext->zone_)
      ext->zone_->used_capacity_-=ext->length_;
    delete ext;
  }
  copied->clear();
  return true;
}
}  // 命名空间结束。
