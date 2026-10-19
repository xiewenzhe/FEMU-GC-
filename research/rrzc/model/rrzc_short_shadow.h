// 独立短期影子选择器：只生成建议，禁止接入真实迁移。
#pragma once
#include "rrzc_v1_growth.h"

namespace rrzc_v1 {
inline bool ShortShadowRequested(const char* value) {
  return value && std::strcmp(value,"1")==0;
}
inline bool ShortShadowConflict(const char* value, const char* real) {
  return ShortShadowRequested(value) && ShortShadowRequested(real);
}
inline bool ShortShadowEnabled(const char* value, bool observe, bool execute,
                               bool sampled) {
  return ShortShadowRequested(value) && observe && !execute && sampled;
}
inline Estimate ShortTimeToThreshold(const RecentBlock& b,
                                    const GrowthEvidence& g, uint64_t threshold) {
  // 同块及Reset检查沿用已有增长证据；未知时不借用原窗口补造短期速率。
  if (!threshold || !(b.flags & kEnabled) || !g.known) return {};
  if (b.rc>=threshold) return {true,0};
  return {true,g.delta ? g.predicted_ns : UINT64_MAX};
}
inline ShadowDecision EvaluateShortShadow(
    const std::vector<rrzc_simple::Candidate>& candidates,
    const std::map<uint64_t,RecentBlock>& usable,
    const std::map<uint64_t,GrowthEvidence>& evidence, uint64_t threshold,
    uint64_t reclaim_budget, uint64_t horizon_ns, uint64_t extra_budget) {
  ShadowDecision out;
  out.greedy=rrzc_simple::Choose(candidates,reclaim_budget,false);
  if (!out.greedy.found) return out;
  out.proposed_zone=out.greedy.selected.zone;
  if (!threshold || !horizon_ns || horizon_ns>300*kSecond) {
    out.reason="短期影子参数不合法"; return out;
  }
  auto estimate=[&](uint64_t zone) {
    const auto b=usable.find(zone); const auto g=evidence.find(zone);
    return b!=usable.end() && g!=evidence.end() ?
        ShortTimeToThreshold(b->second,g->second,threshold) : Estimate{};
  };
  const auto greedy_eta=estimate(out.proposed_zone);
  if (!greedy_eta.known) { out.reason="Greedy同块近期统计未知"; return out; }
  if (greedy_eta.threshold_ns<=horizon_ns) {
    out.reason="Greedy同块近期已经紧迫";
    out.predicted_ns=greedy_eta.threshold_ns; return out;
  }
  out.reason="没有同块近期紧迫候选";
  uint64_t best_valid=UINT64_MAX;
  for (const auto& c:candidates) {
    if (!rrzc_simple::Eligible(c,reclaim_budget) || c.zone==out.greedy.selected.zone) continue;
    const auto eta=estimate(c.zone);
    if (!eta.known || eta.threshold_ns>horizon_ns) continue;
    const uint64_t extra=c.valid>out.greedy.selected.valid ? c.valid-out.greedy.selected.valid : 0;
    if (extra>extra_budget) {
      if (!out.would_switch) out.reason="短期紧迫候选超出额外有效搬运预算";
      continue;
    }
    if (!out.would_switch || eta.threshold_ns<out.predicted_ns ||
        (eta.threshold_ns==out.predicted_ns && (c.valid<best_valid ||
         (c.valid==best_valid && c.zone<out.proposed_zone)))) {
      out.would_switch=true; out.proposed_zone=c.zone; out.extra_valid=extra;
      out.predicted_ns=eta.threshold_ns; best_valid=c.valid;
      out.reason="同块近期紧迫且额外有效搬运在预算内";
    }
  }
  return out;
}
}  // 命名空间结束。
