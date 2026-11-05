// 独立实验：复用V2预测和净成本，用收益约束额外搬运，不修改旧固定预算入口。
#pragma once
#include "rrzc_net_cost_selector.h"

namespace rrzc_benefit_budget {
enum class Mode { Off, Observe, Execute };
inline Mode Parse(const char* request,bool sampling,bool guarded_execute,
                  bool old_execute,bool cost_rank,bool tail_ablation,bool v2_requested) {
  if (!request || !sampling || old_execute || cost_rank || tail_ablation || v2_requested)
    return Mode::Off;
  // 观察必须配合Greedy影子入口，不能把观察误配为真实执行。
  if (!std::strcmp(request,"observe") && !guarded_execute) return Mode::Observe;
  if (!std::strcmp(request,"execute") && guarded_execute) return Mode::Execute;
  return Mode::Off;
}
struct Allowance {
  bool known=false;
  uint64_t bytes=0;
};
inline Allowance CreditAllowance(const rrzc_simple::Candidate& greedy,
    const rrzc_simple::Candidate& candidate,const rrzc_net_cost::Cost& cost) {
  if (!cost.known || !cost.credit || greedy.capacity!=candidate.capacity ||
      !greedy.valid || greedy.valid>=greedy.capacity || candidate.valid<greedy.valid ||
      candidate.valid>=candidate.capacity || cost.credit>candidate.valid ||
      cost.net_copy!=candidate.valid-cost.credit || cost.reclaimed!=candidate.capacity-candidate.valid)
    return {};
  // 严格要求：C*(Vc-Vg) < B*(C-Vg)。减一后除法精确表达整数严格不等式。
  const __uint128_t product=__uint128_t(cost.credit)*(greedy.capacity-greedy.valid);
  return {true,uint64_t((product-1)/greedy.capacity)};
}
struct Report {
  rrzc_net_cost::Report net;
  uint64_t guard_budget=0,eligible_extra_upper=0;
};
inline Report Evaluate(const std::vector<rrzc_simple::Candidate>& candidates,
    const std::map<uint64_t,rrzc_v1::RecentBlock>& usable,
    const std::map<uint64_t,rrzc_v1::GrowthEvidence>& growth,
    const std::map<uint64_t,rrzc_retention::Forecast>& forecasts,
    uint64_t threshold,uint64_t reclaim,uint64_t horizon,uint64_t legacy_budget) {
  Report report;report.guard_budget=legacy_budget;
  const auto greedy=rrzc_simple::Choose(candidates,reclaim,false);
  report.net.decision.greedy=greedy;
  if (!greedy.found) return report;
  report.net.decision.proposed_zone=greedy.selected.zone;
  // 只从原合格集合推导枚举上界；不放宽回收资格，不设置新的经验预算。
  for (const auto& c:candidates) {
    if (!rrzc_simple::Eligible(c,reclaim)) continue;
    if (c.capacity!=greedy.selected.capacity) {
      report.net.decision.reason="收益预算仅支持相同Zone容量，保持Greedy";return report;
    }
    report.eligible_extra_upper=std::max(report.eligible_extra_upper,c.valid-greedy.selected.valid);
  }
  report.net=rrzc_net_cost::Evaluate(candidates,usable,growth,forecasts,
      threshold,reclaim,horizon,report.eligible_extra_upper);
  auto& decision=report.net.decision;
  if (!decision.would_switch) return report;
  const auto chosen=std::find_if(candidates.begin(),candidates.end(),
      [&](const rrzc_simple::Candidate& c){return c.zone==decision.proposed_zone;});
  const auto cost=report.net.costs.find(decision.proposed_zone);
  const auto allowance=(chosen!=candidates.end() && cost!=report.net.costs.end()) ?
      CreditAllowance(greedy.selected,*chosen,cost->second) : Allowance{};
  if (!allowance.known || decision.extra_valid>allowance.bytes) {
    decision.proposed_zone=greedy.selected.zone;decision.would_switch=false;
    decision.extra_valid=0;decision.predicted_ns=UINT64_MAX;
    decision.reason="净收益预算复核不通过，保持Greedy";return report;
  }
  // 传给既有执行复核，避免旧16MiB门槛在评分之后再次否决合法建议。
  report.guard_budget=std::min(allowance.bytes,report.eligible_extra_upper);
  decision.reason="RR抵扣覆盖额外搬运与回收空间损失，进入原实时复核";
  return report;
}
}  // 收益预算实验命名空间。
