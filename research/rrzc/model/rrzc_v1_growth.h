// 独立、默认关闭的改选准入；不替换原十秒/三十秒预测或空间预算。
#pragma once
#include "rrzc_urgency_selector.h"
#include <cstring>
#include <deque>

namespace rrzc_v1 {
constexpr uint64_t kGrowthMinNs=kSecond/2, kGrowthMaxNs=2*kSecond;
inline bool GrowthEnabled(const char* value, bool observe) {
  return observe && value && std::strcmp(value,"1")==0;
}
struct GrowthEvidence {
  bool known=false, pass=false, has_previous=false;
  uint64_t delta=0, interval_ns=0, predicted_ns=UINT64_MAX;
  uint64_t previous_session_hi=0, previous_session_lo=0;
  RecentBlock previous;
  const char* reason="缺少足够早的采样";
};
// 最多保存四帧全区快照；只采用最新点，不回退到更旧的突发历史。
class GrowthHistory {
 public:
  void Clear() { frames_.clear(); }
  void Push(const RecentReport& report) {
    if (!frames_.empty()) {
      const auto& last=frames_.back();
      if (last.session_hi!=report.session_hi || last.session_lo!=report.session_lo ||
          last.threshold!=report.threshold || last.blocks_per_zone!=report.blocks_per_zone)
        Clear();
    }
    frames_.push_back(report);
    if (frames_.size()>4) frames_.pop_front();
  }
  GrowthEvidence Check(const RecentReport& current, const RecentBlock& b,
                       uint64_t horizon_ns, bool aligned=false) const {
    GrowthEvidence out;
    if (!current.threshold || !horizon_ns || horizon_ns>300*kSecond ||
        !(b.flags & kEnabled)) { out.reason="当前统计或参数无效"; return out; }
    // 默认仍按原最新点规则；新对齐入口只越过已验证同块且单调的过近点，
    // 使用第一个满足固定时间窗口的点，不跳过换块、Reset或有效的零增长点。
    uint64_t upper_time=b.snapshot_ns, upper_rc=b.rc;
    for (auto frame=frames_.rbegin(); frame!=frames_.rend(); ++frame) {
      auto p=std::find_if(frame->zones.begin(),frame->zones.end(),
          [&](const RecentBlock& x){return x.zone==b.zone;});
      if (p==frame->zones.end()) { out.reason="采样区缺失"; return out; }
      out.has_previous=true; out.previous=*p;
      out.previous_session_hi=frame->session_hi;
      out.previous_session_lo=frame->session_lo;
      if (frame->session_hi!=current.session_hi || frame->session_lo!=current.session_lo ||
          frame->threshold!=current.threshold || frame->blocks_per_zone!=current.blocks_per_zone ||
          p->generation!=b.generation || p->epoch_start_ns!=b.epoch_start_ns ||
          p->zone_index!=b.zone_index || p->block_id!=b.block_id || p->ch!=b.ch ||
          p->lun!=b.lun || p->plane!=b.plane || p->block!=b.block || !(p->flags & kEnabled)) {
        out.reason="会话代际或块身份变化"; return out;
      }
      if (p->snapshot_ns>=upper_time || p->rc>upper_rc) {
        out.reason="时间或读数不单调"; return out;
      }
      upper_time=p->snapshot_ns; upper_rc=p->rc;
      out.interval_ns=b.snapshot_ns-p->snapshot_ns;
      if (out.interval_ns<kGrowthMinNs) {
        out.reason="最新采样过近";
        if (aligned) continue;
        return out;
      }
      if (out.interval_ns>kGrowthMaxNs) { out.reason="采样过旧"; return out; }
      out.delta=b.rc-p->rc; out.known=true;
      if (!out.delta) { out.reason="近期没有持续读取"; return out; }
      const __uint128_t numerator=__uint128_t(b.rc>=current.threshold ? 0 : current.threshold-b.rc)*out.interval_ns;
      const __uint128_t eta=(numerator+out.delta-1)/out.delta;
      out.predicted_ns=eta>=UINT64_MAX ? UINT64_MAX : uint64_t(eta);
      out.pass=out.predicted_ns<=horizon_ns;
      out.reason=out.pass ? "同块近期增量支持界限内达阈值" : "近期读取不足以支持界限内达阈值";
      return out;
    }
    return out;
  }
 private:
  std::deque<RecentReport> frames_;
};
struct GrowthDecision {
  ShadowDecision original, admitted;
  std::map<uint64_t,GrowthEvidence> evidence;
};
inline GrowthDecision EvaluateWithGrowth(
    const std::vector<rrzc_simple::Candidate>& candidates,
    const std::map<uint64_t,RecentBlock>& usable, const RecentReport& current,
    const GrowthHistory& history, uint64_t reclaim_budget,
    uint64_t horizon_ns, uint64_t extra_budget, bool aligned=false) {
  GrowthDecision out;
  out.original=Evaluate(candidates,usable,current.threshold,reclaim_budget,horizon_ns,extra_budget);
  std::map<uint64_t,RecentBlock> admitted;
  for (const auto& c:candidates) {
    const auto p=usable.find(c.zone);
    GrowthEvidence gate;
    if (p!=usable.end()) gate=history.Check(current,p->second,horizon_ns,aligned);
    else gate.reason="原统计准入未通过";
    out.evidence.emplace(c.zone,gate);
    // Greedy仍按原有效量选择；过滤的只是其他候选的改选许可。
    if (p!=usable.end() && (gate.pass ||
        (out.original.greedy.found && c.zone==out.original.greedy.selected.zone)))
      admitted.emplace(c.zone,p->second);
  }
  out.admitted=Evaluate(candidates,admitted,current.threshold,reclaim_budget,horizon_ns,extra_budget);
  if (out.original.would_switch && !out.admitted.would_switch)
    out.admitted.reason="原紧迫建议未通过同块持续增长门槛";
  return out;
}
}  // 命名空间结束。
