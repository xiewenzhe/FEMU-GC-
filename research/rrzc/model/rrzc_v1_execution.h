// V1真实选区的独立入口；不改变影子预测规则或历史加权选择器。
#pragma once
#include "rrzc_urgency_selector.h"
#include <cstring>

namespace rrzc_v1 {
struct ExecutionMode {
  bool observe = false, execute = false;
};
inline ExecutionMode ResolveMode(const char* shadow, const char* real,
                                 bool valid_greedy, bool manual_requested) {
  const bool s = shadow && std::strcmp(shadow, "1") == 0;
  const bool r = real && std::strcmp(real, "1") == 0;
  if (!valid_greedy || manual_requested || (s && r)) return {};
  return {s || r, r};
}
struct ExecutionDecision {
  rrzc_simple::Decision actual;
  bool switched = false;
  uint64_t extra_valid = 0;
  const char* reason = "未启用真实改选";
};
// 选择前复核只负责拒绝过期快照；不重新预测、不新增候选或放宽预算。
template<class Validate>
inline ExecutionDecision ApplyDecision(
    const rrzc_simple::Decision& greedy, const ShadowDecision& proposal,
    const std::vector<rrzc_simple::Candidate>& candidates,
    uint64_t reclaim_budget, uint64_t extra_budget, bool enabled,
    Validate validate) {
  ExecutionDecision out;
  out.actual = greedy;
  if (!enabled) return out;
  out.reason = proposal.reason;
  if (!greedy.found || !proposal.would_switch) return out;
  if (!proposal.greedy.found || proposal.greedy.selected.zone != greedy.selected.zone ||
      proposal.greedy.selected.valid != greedy.selected.valid) {
    out.reason = "Greedy基准不一致，保持Greedy";
    return out;
  }
  const auto chosen = std::find_if(candidates.begin(), candidates.end(),
      [&](const rrzc_simple::Candidate& c) { return c.zone == proposal.proposed_zone; });
  if (chosen == candidates.end() || chosen->zone == greedy.selected.zone ||
      !rrzc_simple::Eligible(*chosen, reclaim_budget)) {
    out.reason = "建议不在原合格候选内，保持Greedy";
    return out;
  }
  const uint64_t extra = chosen->valid > greedy.selected.valid ?
      chosen->valid - greedy.selected.valid : 0;
  if (extra != proposal.extra_valid || extra > extra_budget) {
    out.reason = "额外有效搬运预算复核失败，保持Greedy";
    return out;
  }
  if (!validate(greedy.selected) || !validate(*chosen)) {
    out.reason = "选择前快照复核失败，保持Greedy";
    return out;
  }
  out.actual = rrzc_simple::Choose({*chosen}, reclaim_budget, false);
  out.switched = true;
  out.extra_valid = extra;
  return out;
}
}  // 命名空间结束。
