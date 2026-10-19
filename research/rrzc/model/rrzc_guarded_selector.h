// 独立默认关闭的短期紧迫度/预算/尾部约束入口；不替换旧选择器。
#pragma once
#include "rrzc_short_shadow.h"
#include "rrzc_cost_observe.h"
#include "rrzc_v1_execution.h"

namespace rrzc_guard {
struct Mode { bool requested=false, observe=false, execute=false; };
inline Mode Resolve(const char* value,bool greedy,bool manual,bool legacy) {
  if (!value || !std::strcmp(value,"0") || !std::strcmp(value,"off")) return {};
  Mode out; out.requested=true;
  if (!greedy || manual || legacy) return out;
  out.observe=!std::strcmp(value,"shadow") || !std::strcmp(value,"execute");
  out.execute=out.observe && !std::strcmp(value,"execute"); return out;
}
struct Bounds {
  bool known=true;
  uint64_t count=0, covered=0, first_lower=0, first_upper=0, lower=0, upper=0;
  void Add(uint64_t length,uint64_t alignment,uint64_t header,uint64_t capacity) {
    const auto b=rrzc_cost::BoundPhysical(length,alignment,header);
    if (!count) { first_lower=b.lower; first_upper=b.upper; }
    ++count;
    const __uint128_t new_covered=__uint128_t(covered)+length;
    const __uint128_t new_lower=__uint128_t(lower)+b.lower;
    const __uint128_t new_upper=__uint128_t(upper)+b.upper;
    known=known && b.known && b.upper<=capacity && new_covered<=UINT64_MAX &&
        new_lower<=UINT64_MAX && new_upper<=UINT64_MAX;
    covered=uint64_t(new_covered); lower=uint64_t(new_lower); upper=uint64_t(new_upper);
  }
  bool Complete(uint64_t valid) const { return known && count && covered==valid; }
};
struct TailDecision {
  bool allow=false;
  const char* category="未知回退";
  const char* reason="片段物理界限或完整覆盖未知";
};
inline TailDecision Evaluate(const Bounds& g,uint64_t gv,const Bounds& c,uint64_t cv,
                            const rrzc_cost::ReserveObservation& r,uint64_t capacity) {
  if (!g.Complete(gv) || !c.Complete(cv)) return {};
  if (!r.enabled || !r.known || !r.present)
    return {false,"未知回退","当前预留未知或不存在，不推断未来分配"};
  if (!capacity || r.remaining>capacity || r.valid>capacity ||
      __uint128_t(r.remaining)+r.valid>capacity)
    return {false,"未知回退","预留容量字段不符"};
  if (c.upper<=r.remaining)
    return {true,"尾部不否决","建议全部片段物理上界可容纳于当前尾部"};
  if (!r.valid && c.upper<=capacity)
    return {true,"尾部不否决","预留无有效数据，原分配路径可Reset复用"};
  // 普通部分目标目前未知；这里只保守保护预留，不宣称必然Finish或多搬。
  if (g.first_upper<=r.remaining && c.first_lower>r.remaining)
    return {false,"预留压力保守拦截","Greedy首片段确定可装入而建议确定装不入"};
  if (g.first_lower<=r.remaining && r.remaining<g.first_upper && c.first_lower>r.remaining)
    return {false,"未知回退","Greedy稀疏头边界未知，建议首片段确定装不入"};
  if (c.first_lower<=r.remaining && r.remaining<c.first_upper)
    return {false,"未知回退","建议首片段位于稀疏头边界，实际申请量未知"};
  if (g.first_lower>r.remaining && c.first_lower>r.remaining)
    return {false,"未知回退","两者首片段均不适配，不能确认新增尾部成本"};
  return {false,"未知回退","建议全部片段不能保证容纳，后续分配未知"};
}
inline bool StableReserve(const rrzc_cost::ReserveObservation& a,
                          const rrzc_cost::ReserveObservation& b) {
  return a.enabled && b.enabled && a.known && b.known && a.present && b.present &&
      a.zone==b.zone && a.remaining==b.remaining && a.valid==b.valid && a.wp==b.wp &&
      a.host_generation==b.host_generation && a.host_sequence==b.host_sequence;
}
template<class Validate>
inline rrzc_v1::ExecutionDecision Apply(const rrzc_simple::Decision& greedy,
    const rrzc_v1::ShadowDecision& proposal,const std::vector<rrzc_simple::Candidate>& candidates,
    uint64_t reclaim_budget,uint64_t extra_budget,const TailDecision& tail,
    const rrzc_cost::ReserveObservation& before,const rrzc_cost::ReserveObservation& after,
    Validate validate) {
  auto permitted=proposal;
  if (proposal.would_switch && !tail.allow) {
    permitted.would_switch=false; permitted.extra_valid=0; permitted.reason=tail.reason;
  }
  return rrzc_v1::ApplyDecision(greedy,permitted,candidates,reclaim_budget,extra_budget,true,
      [&](const rrzc_simple::Candidate& c){return StableReserve(before,after) && validate(c);});
}
}  // 命名空间结束。
