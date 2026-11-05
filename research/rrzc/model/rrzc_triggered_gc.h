// 已触发GC的选区适配；不改变空间需求统计和旧选择器的零需求语义。
#pragma once
#include "rrzc_space_plan.h"

namespace rrzc_triggered_gc {
inline rrzc_space_plan::Need NeedForSelection(
    const rrzc_space_plan::Need& observed, bool gc_triggered) {
  auto selection = observed;
  // 一字节仅表示本轮须有正回收进展，不是实际空间缺口或新的候选门槛。
  // 安全资格、候选有效性和有无可选区，仍由原选择器核验。
  if (gc_triggered && selection.known && !selection.bytes) selection.bytes = 1;
  return selection;
}
}  // 已触发GC的选区适配结束。
