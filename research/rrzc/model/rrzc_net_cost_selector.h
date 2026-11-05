// V2实验：单位回收空间净迁移成本。时间折扣是偏好，不是RR概率或收益保证。
#pragma once
#include "rrzc_retention_selector.h"

namespace rrzc_net_cost {
using rrzc_v1::kSecond;
enum class Mode { Off, Observe, Execute };
inline Mode Parse(const char* request, bool sampling, bool guarded_execute,
                  bool old_execute, bool cost_rank, bool tail_ablation) {
  if (!request || !sampling || old_execute || cost_rank || tail_ablation) return Mode::Off;
  if (!std::strcmp(request,"observe")) return Mode::Observe;
  if (!std::strcmp(request,"execute") && guarded_execute) return Mode::Execute;
  return Mode::Off;
}
struct Cost {
  bool known=false;
  uint64_t eta_ns=UINT64_MAX, retained=0, credit=0, net_copy=0, reclaimed=0;
  const char* reason="没有可用预测";
};
// 两段有效量历史取较快退役速率，向上取整；减小可能高估的RR迁移收益。
// 同代际、连续性和迁移污染由既有History::Check核验，这里不跨代际拼接。
inline Cost Estimate(const rrzc_simple::Candidate& c, const rrzc_v1::RecentBlock& b,
                     const rrzc_v1::GrowthEvidence& growth,
                     const rrzc_retention::Forecast& f, uint64_t threshold,
                     uint64_t horizon) {
  Cost out;
  if (!threshold || horizon!=10*kSecond || !c.valid || c.valid>=c.capacity ||
      b.zone!=c.zone || !(b.flags & rrzc_v1::kEnabled)) {
    out.reason="参数或块所属Zone不合法"; return out;
  }
  out.reclaimed=c.capacity-c.valid;
  const auto short_eta=rrzc_v1::ShortTimeToThreshold(b,growth,threshold);
  const auto long_eta=rrzc_v1::TimeToThreshold(b,threshold);
  if (!short_eta.known || !long_eta.known || (b.flags & rrzc_v1::kLoss)) {
    out.reason="同块短期或完整窗口预测未知"; return out;
  }
  // 至少两种尺度均支持近期达阈值，避免仅由单次短突发给予收益。
  out.eta_ns=std::max(short_eta.threshold_ns,long_eta.threshold_ns);
  if (out.eta_ns>horizon) { out.reason="多尺度预测不支持十秒内RR"; return out; }
  if (!f.known || f.interval10<10*kSecond || f.interval10>12*kSecond ||
      f.interval30<30*kSecond || f.interval30>32*kSecond || f.loss10>f.loss30 ||
      f.retained>c.valid) {
    out.reason="有效量连续历史未知或不合法"; return out;
  }
  auto loss=[&](uint64_t bytes,uint64_t interval) {
    const __uint128_t value=__uint128_t(bytes)*out.eta_ns;
    return std::min<__uint128_t>(c.valid,(value+interval-1)/interval);
  };
  out.retained=c.valid-uint64_t(std::max(loss(f.loss10,f.interval10),loss(f.loss30,f.interval30)));
  // 固定H内的线性时间偏好：越接近当前，给予越完整的潜在RR复制抵扣。
  // ETA恰好等于H时抵扣为零；不把线性折扣包装成经校准的概率。
  out.credit=uint64_t(__uint128_t(out.retained)*(horizon-out.eta_ns)/horizon);
  out.net_copy=c.valid-out.credit;
  out.known=true;
  out.reason="多尺度紧迫度与到RR时保留量的时间折扣";
  return out;
}
inline bool Less(const Cost& a,const Cost& b) {
  return __uint128_t(a.net_copy)*b.reclaimed < __uint128_t(b.net_copy)*a.reclaimed;
}
struct Report {
  rrzc_v1::ShadowDecision decision;
  std::map<uint64_t,Cost> costs;
};
inline Report Evaluate(const std::vector<rrzc_simple::Candidate>& candidates,
    const std::map<uint64_t,rrzc_v1::RecentBlock>& usable,
    const std::map<uint64_t,rrzc_v1::GrowthEvidence>& growth,
    const std::map<uint64_t,rrzc_retention::Forecast>& forecasts,
    uint64_t threshold,uint64_t reclaim,uint64_t horizon,uint64_t budget) {
  Report report;
  auto& out=report.decision;
  // 原资格、Greedy未知/已紧迫回退、短期准入和16MiB预算不放宽。
  out=rrzc_v1::EvaluateShortShadow(candidates,usable,growth,threshold,reclaim,horizon,budget);
  if (!out.would_switch) return report;
  out.would_switch=false; out.proposed_zone=out.greedy.selected.zone;
  out.extra_valid=0; out.predicted_ns=UINT64_MAX;
  out.reason="没有比Greedy单位回收净成本更低的可信候选";
  const auto& greedy=out.greedy.selected;
  // Greedy在当前窗口不紧迫，不虚构它的RR抵扣；保持未知时保守回退。
  Cost best; best.known=true; best.net_copy=greedy.valid;
  best.reclaimed=greedy.capacity-greedy.valid;
  uint64_t best_valid=greedy.valid;
  for (const auto& c:candidates) {
    if (!rrzc_simple::Eligible(c,reclaim) || c.zone==greedy.zone) continue;
    const uint64_t extra=c.valid>greedy.valid ? c.valid-greedy.valid : 0;
    if (extra>budget) continue;
    const auto b=usable.find(c.zone); const auto g=growth.find(c.zone); const auto f=forecasts.find(c.zone);
    if (b==usable.end() || g==growth.end() || f==forecasts.end()) continue;
    const auto cost=Estimate(c,b->second,g->second,f->second,threshold,horizon);
    report.costs.emplace(c.zone,cost);
    if (!cost.known || !cost.credit) continue;
    const bool tie=out.would_switch && !Less(cost,best) && !Less(best,cost);
    if (Less(cost,best) || (tie && (c.valid<best_valid ||
        (c.valid==best_valid && (cost.eta_ns<out.predicted_ns ||
          (cost.eta_ns==out.predicted_ns && c.zone<out.proposed_zone)))))) {
      best=cost; best_valid=c.valid;
      out.would_switch=true; out.proposed_zone=c.zone; out.extra_valid=extra;
      out.predicted_ns=cost.eta_ns;
      out.reason="时间折扣RR抵扣后的单位回收净成本低于Greedy";
    }
  }
  return report;
}
inline rrzc_v1::ShadowDecision Apply(Mode mode,const rrzc_v1::ShadowDecision& old,
                                    const Report& report) {
  return mode==Mode::Execute ? report.decision : old;
}
}  // V2净成本实验命名空间。
