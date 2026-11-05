// 时序实验核：仅首区立即执行；补充区的RR可能替代ZC，不能当作已刷新或重复收费。
#pragma once
#include "rrzc_space_plan.h"
#include "rrzc_expected_rr_selector.h"
#include <mutex>

namespace rrzc_deadline {
using rrzc_space_plan::Wide;
constexpr uint64_t kScale=rrzc_expected_rr::kProbabilityScale;
constexpr uint64_t kSecond=rrzc_v1::kSecond;
// 独立四帧历史；不改变旧增长核。中间任一点换块、Reset或倒退均拒绝预测。
class History {
 public:
  const std::deque<rrzc_v1::RecentReport>& Frames() const { return frames_; }
  void Clear() { frames_.clear(); }
  void Push(const rrzc_v1::RecentReport& report) {
    if(!frames_.empty()) {
      const auto& last=frames_.back();
      if(last.session_hi!=report.session_hi || last.session_lo!=report.session_lo ||
          last.threshold!=report.threshold || last.blocks_per_zone!=report.blocks_per_zone)Clear();
    }
    frames_.push_back(report);if(frames_.size()>4)frames_.pop_front();
  }
  rrzc_v1::GrowthEvidence Check(const rrzc_v1::RecentReport& current,
      const rrzc_v1::RecentBlock& b,uint64_t horizon) const {
    rrzc_v1::GrowthEvidence out;
    if(!current.threshold || !horizon || horizon>300*kSecond || !(b.flags&rrzc_v1::kEnabled)) {
      out.reason="当前统计或参数无效";return out;
    }
    uint64_t upper_time=b.snapshot_ns,upper_rc=b.rc;
    for(auto frame=frames_.rbegin();frame!=frames_.rend();++frame) {
      auto p=std::find_if(frame->zones.begin(),frame->zones.end(),
          [&](const rrzc_v1::RecentBlock& x){return x.zone==b.zone;});
      if(p==frame->zones.end()){out.reason="采样区缺失";return out;}
      out.has_previous=true;out.previous=*p;
      out.previous_session_hi=frame->session_hi;out.previous_session_lo=frame->session_lo;
      if(frame->session_hi!=current.session_hi || frame->session_lo!=current.session_lo ||
          frame->threshold!=current.threshold || frame->blocks_per_zone!=current.blocks_per_zone ||
          p->generation!=b.generation || p->epoch_start_ns!=b.epoch_start_ns ||
          p->zone_index!=b.zone_index || p->block_id!=b.block_id || p->ch!=b.ch ||
          p->lun!=b.lun || p->plane!=b.plane || p->block!=b.block || !(p->flags&rrzc_v1::kEnabled)) {
        out.reason="会话代际或块身份变化";return out;
      }
      if(p->snapshot_ns>=upper_time || p->rc>upper_rc){out.reason="时间或读数不单调";return out;}
      upper_time=p->snapshot_ns;upper_rc=p->rc;out.interval_ns=b.snapshot_ns-p->snapshot_ns;
      if(out.interval_ns<2*kSecond){out.reason="最新采样过近";continue;}
      if(out.interval_ns>4*kSecond){out.reason="采样过旧";return out;}
      out.delta=b.rc-p->rc;out.known=true;
      if(!out.delta){out.reason="近期没有持续读取";return out;}
      const Wide numerator=Wide(b.rc>=current.threshold?0:current.threshold-b.rc)*out.interval_ns;
      const Wide eta=(numerator+out.delta-1)/out.delta;
      out.predicted_ns=eta>=UINT64_MAX?UINT64_MAX:uint64_t(eta);out.pass=out.predicted_ns<=horizon;
      out.reason=out.pass?"同块近期增量支持界限内达阈值":"近期读取不足以支持界限内达阈值";return out;
    }
    return out;
  }
 private:
  std::deque<rrzc_v1::RecentReport> frames_;
};
// 复用原采样查询的结果；先冻结再发起当前查询，不引入未来样本。
class Store {
 public:
  struct Snapshot {History history;uint64_t sequence=0;};
  void Publish(const rrzc_v1::RecentReport& report,bool good) {
    std::lock_guard<std::mutex> lock(mutex_);
    if(good)history_.Push(report);else history_.Clear();++sequence_;
  }
  Snapshot Freeze() {std::lock_guard<std::mutex> lock(mutex_);return {history_,sequence_};}
 private:
  std::mutex mutex_;
  History history_;
  uint64_t sequence_=0;
};
struct Evidence {
  rrzc_v1::GrowthEvidence growth;
  rrzc_window_credit::Evidence window;
  rrzc_expected_rr::Probability probability;
  const char* reason="统计或留存未知，不预支RR成本";
};
inline rrzc_direct::Cost EstimateEvidence(const rrzc_simple::Candidate& c,
    const rrzc_v1::RecentBlock* b,const rrzc_window_credit::Retention* retention,
    uint64_t threshold,uint64_t horizon,const rrzc_v1::GrowthEvidence& growth,Evidence* evidence=nullptr) {
  Evidence local;auto& e=evidence?*evidence:local;e={};e.growth=growth;
  auto out=rrzc_window_credit::Estimate(c,b,retention,threshold,horizon,&e.window);
  out.credit=0;out.net=c.valid;
  if(!b || !out.risk_known)return out;
  if(b->rc>=threshold) {
    e.probability={true,kScale};out.retained=c.valid;out.credit=c.valid;out.net=0;
    e.reason="当前已到阈值，不冒充提前预测成功";return out;
  }
  // 使用现有四帧中第一个至少两秒的合法端点；任何中间换块或Reset仍使证据失效。
  if(!growth.known || growth.interval_ns<2*kSecond || growth.interval_ns>4*kSecond ||
      growth.interval_ns>b->snapshot_ns-b->epoch_start_ns || growth.delta>b->rc ||
      (growth.interval_ns<=e.window.read_span && growth.delta>b->delta10)) {
    out.risk_known=false;out.eta=UINT64_MAX;out.retained=0;return out;
  }
  const auto eta=rrzc_window_credit::Eta(threshold-b->rc,growth.interval_ns,growth.delta);
  out.eta=std::max(out.eta,eta);out.retained=0;
  if(out.eta==UINT64_MAX){e.reason="至少两秒同块窗口无增长，不借旧突发抵扣";return out;}
  if(!retention || !e.window.window)return out;
  auto loss=[&](uint64_t bytes,uint64_t span) {
    return std::min<Wide>(c.valid,(Wide(bytes)*out.eta+span-1)/span);
  };
  out.retained=c.valid-uint64_t(std::max(loss(retention->short_loss,retention->short_span),
      loss(retention->long_loss,retention->long_span)));
  const long double rate=std::min(static_cast<long double>(b->delta10)/e.window.read_span,
      static_cast<long double>(growth.delta)/growth.interval_ns);
  e.probability=rrzc_expected_rr::ThresholdProbability(threshold-b->rc,rate*e.window.window);
  if(e.probability.known)out.credit=uint64_t(Wide(out.retained)*e.probability.units/kScale);
  out.net=c.valid-out.credit;
  e.reason="两秒以上同块增长及原留存；支撑窗口不扩大，泊松假设未校准";
  return out;
}
inline rrzc_direct::Cost Estimate(const rrzc_simple::Candidate& c,const rrzc_v1::RecentBlock* b,
    const rrzc_window_credit::Retention* f,const rrzc_v1::RecentReport& report,
    const History& history,uint64_t horizon,Evidence* e=nullptr) {
  const auto growth=b?history.Check(report,*b,horizon):rrzc_v1::GrowthEvidence{};
  return EstimateEvidence(c,b,f,report.threshold,horizon,growth,e);
}
struct Input {
  rrzc_direct::Row row;
  uint64_t probability=0,rr_space=0;
};
struct Plan {
  uint64_t first=0,first_copy=0;
  bool sufficient=false;
  Wide copy=0,rr_copy=0,reclaimed=0,rr_space=0;
  std::vector<uint64_t> zones;
  Wide Total() const {return copy+rr_copy;}
};
struct Decision {
  bool found=false,progress_only=false;
  uint64_t zone=0;
  std::vector<Plan> plans;
};
inline bool Better(const Plan& a,const Plan& b) {
  if(a.Total()!=b.Total())return a.Total()<b.Total();
  // RR复制只作为同总复制成本时的强制维护压力次级判据，不伪造字节节省。
  if(a.rr_copy!=b.rr_copy)return a.rr_copy<b.rr_copy;
  if(a.first_copy!=b.first_copy)return a.first_copy<b.first_copy;
  if(a.zones.size()!=b.zones.size())return a.zones.size()<b.zones.size();
  if(a.reclaimed+a.rr_space!=b.reclaimed+b.rr_space)return a.reclaimed+a.rr_space>b.reclaimed+b.rr_space;
  return a.first<b.first;
}
inline Decision Select(const std::vector<Input>& raw,const rrzc_space_plan::Need& need) {
  Decision out;if(need.known && !need.bytes)return out;
  std::vector<const Input*> pool;
  for(const auto& x:raw)if(x.row.safe && x.row.candidate.valid &&
      x.row.candidate.valid<x.row.candidate.capacity && x.probability<=kScale &&
      x.row.cost.credit<=x.row.candidate.valid &&
      x.row.cost.reclaimed==x.row.candidate.capacity-x.row.candidate.valid)pool.push_back(&x);
  std::sort(pool.begin(),pool.end(),[](const Input* a,const Input* b){
    const Wide l=Wide(a->row.candidate.valid)*b->row.cost.reclaimed;
    const Wide r=Wide(b->row.candidate.valid)*a->row.cost.reclaimed;
    return l!=r?l<r:a->row.candidate.valid!=b->row.candidate.valid?
        a->row.candidate.valid<b->row.candidate.valid:a->row.candidate.zone<b->row.candidate.zone;
  });
  for(const auto* first:pool) {
    Plan p;p.first=first->row.candidate.zone;p.first_copy=first->row.candidate.valid;p.copy=p.first_copy;
    p.zones.push_back(p.first);p.reclaimed=first->row.cost.reclaimed;
    for(const auto* x:pool) {
      if(p.reclaimed>=need.bytes)break;
      if(x==first)continue;
      p.zones.push_back(x->row.candidate.zone);p.reclaimed+=x->row.cost.reclaimed;
      // 若在下一轮ZC机会前已经RR，原源区不再需要补做ZC；按互斥分支计算期望复制。
      // 补充ZC的未发生RR分支向上取整，不把每区量化误差冒充收益。
      p.copy+=(Wide(x->row.candidate.valid)*(kScale-x->probability)+kScale-1)/kScale;
    }
    p.sufficient=need.known && p.reclaimed>=need.bytes;
    for(const auto* x:pool)if(x!=first) {
      p.rr_copy+=x->row.cost.credit;p.rr_space+=x->rr_space;
    }
    // 未来RR回收只报告；当前缺口仍由原始正回收量构造，不能用预测空间通过安全准入。
    out.plans.push_back(p);
  }
  const Plan* best=nullptr;
  for(const auto& p:out.plans)if(p.sufficient && (!best || Better(p,*best)))best=&p;
  if(best){out.found=true;out.zone=best->first;return out;}
  // 沿用旧核的未知统计/候选不足进展路径；不能因为预测拒绝空间回收。
  std::vector<rrzc_space_plan::Input> old;
  for(const auto* x:pool)old.push_back({x->row,x->rr_space});
  auto fallback=rrzc_space_plan::Select(old,need,true);
  out.found=fallback.found;out.zone=fallback.zone;out.progress_only=fallback.progress_only;return out;
}
}  // 下一轮维护机会前的RR及互斥补充成本核结束。
