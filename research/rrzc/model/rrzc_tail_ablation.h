// 默认关闭的独立消融：只旁路尾部准入，不改变短期建议、预算或安全复核。
#pragma once
#include "rrzc_guarded_selector.h"

namespace rrzc_tail_ablation {
struct Mode { bool requested=false, enabled=false, bypass=false; };
inline Mode Resolve(const char* value,const rrzc_guard::Mode& guarded) {
  if (!value || !std::strcmp(value,"0")) return {};
  Mode mode; mode.requested=true;
  if (!guarded.observe || !guarded.execute) return mode;
  const bool keep=!std::strcmp(value,"keep");
  const bool bypass=!std::strcmp(value,"bypass");
  mode.enabled=keep || bypass;
  mode.bypass=mode.enabled && bypass;
  return mode;
}
inline rrzc_guard::TailDecision EffectiveTail(const rrzc_guard::TailDecision& original,
                                            const Mode& mode,bool would_switch) {
  if (!mode.enabled || !mode.bypass || !would_switch) return original;
  // 后续仍调用原Apply，保留预留稳定性及Greedy/建议源复核；不伪造原尾部结论。
  return {true,"消融旁路","仅实验旁路尾部准入，其余短期建议、预算和稳定性复核不变"};
}
}  // 命名空间结束。
