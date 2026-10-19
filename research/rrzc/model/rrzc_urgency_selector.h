// V1只用于影子评估：实际选区仍调用原有效量Greedy，不启用改选。
#pragma once
#include "rrzc_recent_log.h"
#include "rrzc_weighted_selector.h"
#include <algorithm>
#include <map>

namespace rrzc_v1 {
struct Estimate { bool known = false; uint64_t threshold_ns = UINT64_MAX; };
inline Estimate TimeToThreshold(const RecentBlock& b, uint64_t threshold) {
  if (!threshold || !(b.flags & kEnabled)) return {};
  if (b.rc >= threshold) return {true,0};
  if ((b.flags & (kValid10|kValid30)) != (kValid10|kValid30)) return {};
  const __uint128_t reads30 = std::min(__uint128_t(b.delta10)*3,
                                      __uint128_t(b.delta30));
  if (!reads30) return {true,UINT64_MAX};
  const __uint128_t numerator = __uint128_t(threshold-b.rc)*30*kSecond;
  const __uint128_t eta = (numerator+reads30-1)/reads30;
  return {true,eta >= UINT64_MAX ? UINT64_MAX : uint64_t(eta)};
}
struct ShadowDecision {
  rrzc_simple::Decision greedy;
  uint64_t proposed_zone = 0, extra_valid = 0, predicted_ns = UINT64_MAX;
  bool would_switch = false;
  const char* reason = "没有合格候选";
};
inline ShadowDecision Evaluate(const std::vector<rrzc_simple::Candidate>& candidates,
                               const std::map<uint64_t,RecentBlock>& recent,
                               uint64_t threshold, uint64_t reclaim_budget,
                               uint64_t horizon_ns, uint64_t extra_budget) {
  ShadowDecision out;
  out.greedy = rrzc_simple::Choose(candidates,reclaim_budget,false);
  if (!out.greedy.found) return out;
  out.proposed_zone = out.greedy.selected.zone;
  auto g = recent.find(out.proposed_zone);
  if (!horizon_ns || horizon_ns > 300*kSecond) {
    out.reason="紧迫度参数不合法"; return out;
  }
  if (g == recent.end()) { out.reason="Greedy近期统计未知"; return out; }
  const auto ge = TimeToThreshold(g->second,threshold);
  if (!ge.known) { out.reason="Greedy窗口不完整"; return out; }
  if (ge.threshold_ns <= horizon_ns) {
    out.reason="Greedy自身已经紧迫"; out.predicted_ns=ge.threshold_ns; return out;
  }
  out.reason="没有近期紧迫候选";
  uint64_t best_valid=UINT64_MAX;
  for (const auto& c : candidates) {
    if (!rrzc_simple::Eligible(c,reclaim_budget) || c.zone==out.greedy.selected.zone) continue;
    auto p=recent.find(c.zone);
    if (p==recent.end()) continue;
    const auto e=TimeToThreshold(p->second,threshold);
    if (!e.known || e.threshold_ns>horizon_ns) continue;
    const uint64_t extra=c.valid>out.greedy.selected.valid ? c.valid-out.greedy.selected.valid : 0;
    if (extra>extra_budget) {
      if (!out.would_switch) out.reason="紧迫候选超出额外有效搬运预算";
      continue;
    }
    if (!out.would_switch || e.threshold_ns<out.predicted_ns ||
        (e.threshold_ns==out.predicted_ns &&
         (c.valid<best_valid || (c.valid==best_valid && c.zone<out.proposed_zone)))) {
      out.would_switch=true; out.proposed_zone=c.zone; out.extra_valid=extra;
      out.predicted_ns=e.threshold_ns; best_valid=c.valid;
      out.reason="近期紧迫且额外有效搬运在预算内";
    }
  }
  return out;
}
}  // 命名空间结束。
