// 默认关闭的实验排序：只在原短期准入集合中比较当前回收成本。
#pragma once
#include "rrzc_guarded_selector.h"
namespace rrzc_rank {
inline bool Enabled(const char* value,const rrzc_guard::Mode& guarded,bool tail_requested) {
  return value && !std::strcmp(value,"1") && guarded.observe && guarded.execute && !tail_requested;
}
inline bool LessCost(const rrzc_simple::Candidate& a,const rrzc_simple::Candidate& b) {
  // 交叉相乘避免浮点舍入；合格候选必须具有正回收空间。
  return __uint128_t(a.valid)*(b.capacity-b.valid)<__uint128_t(b.valid)*(a.capacity-a.valid);
}
inline rrzc_v1::ShadowDecision Evaluate(bool enabled,
    const std::vector<rrzc_simple::Candidate>& candidates,
    const std::map<uint64_t,rrzc_v1::RecentBlock>& usable,
    const std::map<uint64_t,rrzc_v1::GrowthEvidence>& evidence,
    uint64_t threshold,uint64_t reclaim,uint64_t horizon,uint64_t budget) {
  auto out=rrzc_v1::EvaluateShortShadow(candidates,usable,evidence,threshold,reclaim,horizon,budget);
  // 原逻辑负责Greedy未知／已紧迫、参数非法和没有准入候选时的回退。
  if (!enabled || !out.would_switch) return out;
  const rrzc_simple::Candidate* best=nullptr;
  uint64_t best_eta=UINT64_MAX;
  for (const auto& c:candidates) {
    if (!rrzc_simple::Eligible(c,reclaim) || c.valid>=c.capacity || c.zone==out.greedy.selected.zone) continue;
    const auto b=usable.find(c.zone); const auto g=evidence.find(c.zone);
    if (b==usable.end() || g==evidence.end()) continue;
    const auto eta=rrzc_v1::ShortTimeToThreshold(b->second,g->second,threshold);
    const uint64_t extra=c.valid>out.greedy.selected.valid?c.valid-out.greedy.selected.valid:0;
    if (!eta.known || eta.threshold_ns>horizon || extra>budget) continue;
    const bool equal=best && !LessCost(c,*best) && !LessCost(*best,c);
    if (!best || LessCost(c,*best) || (equal && (eta.threshold_ns<best_eta ||
        (eta.threshold_ns==best_eta && c.zone<best->zone)))) {
      best=&c; best_eta=eta.threshold_ns;
    }
  }
  if (best) {
    out.proposed_zone=best->zone; out.predicted_ns=best_eta;
    out.extra_valid=best->valid>out.greedy.selected.valid?best->valid-out.greedy.selected.valid:0;
    out.reason="同块近期准入后优先较低单位空间回收成本";
  }
  return out;
}
}  // 命名空间结束。
