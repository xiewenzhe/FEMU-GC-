// 独立时间一致性实验核；旧时序核、读速率估计和实际RR路径保持不变。
#pragma once
#include "rrzc_deadline_selector.h"

namespace rrzc_joint_time {
using rrzc_deadline::Wide;
constexpr uint64_t kScale=rrzc_deadline::kScale;
constexpr uint64_t kSecond=rrzc_deadline::kSecond;
struct Evidence {
  rrzc_deadline::Evidence base;
  uint64_t lifetime_ns=UINT64_MAX,event_window_ns=0,conditional_ns=0;
};
// 已知窗口内到达阈值时的条件平均时间。用归一化泊松尾项求条件均值，
// 避免罕见事件的两个已量化概率相除；不是更换或校准读取过程假设。
inline bool ConditionalTime(uint64_t margin,long double mean,uint64_t window,uint64_t* ns) {
  *ns=0;
  const auto limit=rrzc_expected_rr::kMaxTerms;
  if(!margin || !window || !(mean>0) || !std::isfinite(mean) || mean>limit || margin>limit)return false;
  const uint64_t start=std::max(margin,uint64_t(mean));
  long double sum=1,moment=static_cast<long double>(margin)/(start+1),term=1;
  uint64_t steps=0;
  for(uint64_t n=start;n>margin;--n) {
    if(++steps>=limit)return false;
    term*=n/mean;sum+=term;moment+=term*margin/n;
  }
  term=1;uint64_t n=start;bool done=false;
  while(++steps<limit) {
    term*=mean/(++n);sum+=term;moment+=term*margin/(n+1);
    if(term<1e-18L*sum){done=true;break;}
  }
  if(!done || !std::isfinite(moment) || !(sum>0))return false;
  const auto time=std::ceil(static_cast<long double>(window)*moment/sum);
  if(!std::isfinite(time) || time<0)return false;
  *ns=std::min(window,std::max(uint64_t(1),uint64_t(time)));return true;
}
inline rrzc_direct::Cost EstimateEvidence(const rrzc_simple::Candidate& c,
    const rrzc_v1::RecentBlock* b,const rrzc_window_credit::Retention* f,
    uint64_t threshold,uint64_t horizon,const rrzc_v1::GrowthEvidence& growth,Evidence* evidence=nullptr) {
  Evidence local;auto& e=evidence?*evidence:local;e={};
  auto out=rrzc_deadline::EstimateEvidence(c,b,f,threshold,horizon,growth,&e.base);
  if(!b || !out.risk_known)return out;
  if(b->rc>=threshold){e.lifetime_ns=0;return out;}
  out.retained=out.credit=0;out.net=c.valid;e.base.probability={};
  if(out.eta==UINT64_MAX || !f || !e.base.window.window)return out;
  // 沿用已观察的较快有效量减少速度。事件窗口截止于原支撑窗口或预计全部失效，
  // 不能把失效后到阈值的概率作为“需要搬有效数据RR”去免掉补充ZC成本。
  auto lifetime=[&](uint64_t loss,uint64_t span) {
    if(!loss)return UINT64_MAX;
    const Wide value=Wide(c.valid)*span/loss;
    return value>=UINT64_MAX?UINT64_MAX:uint64_t(value);
  };
  e.lifetime_ns=std::min(lifetime(f->short_loss,f->short_span),lifetime(f->long_loss,f->long_span));
  e.event_window_ns=std::min(e.base.window.window,e.lifetime_ns);
  const long double rate=std::min(static_cast<long double>(b->delta10)/e.base.window.read_span,
      static_cast<long double>(growth.delta)/growth.interval_ns);
  const auto probability=rrzc_expected_rr::ThresholdProbability(threshold-b->rc,rate*e.event_window_ns);
  if(!probability.known)return out;
  if(!probability.units){e.base.probability=probability;e.base.reason="存活支撑窗口内RR概率低于字节评分分辨率";return out;}
  if(!ConditionalTime(threshold-b->rc,rate*e.event_window_ns,e.event_window_ns,&e.conditional_ns)) {
    e.base.reason="条件时间计算未知，不抵扣RR或补充ZC";return out;
  }
  auto loss=[&](uint64_t bytes,uint64_t span) {
    return std::min<Wide>(c.valid,(Wide(bytes)*e.conditional_ns+span-1)/span);
  };
  out.retained=c.valid-uint64_t(std::max(loss(f->short_loss,f->short_span),loss(f->long_loss,f->long_span)));
  e.base.probability=probability;out.credit=uint64_t(Wide(out.retained)*probability.units/kScale);
  out.net=c.valid-out.credit;
  e.base.reason="同一存活窗口的RR概率及条件到达时间；线性留存和泊松假设未校准";
  return out;
}
inline rrzc_direct::Cost Estimate(const rrzc_simple::Candidate& c,const rrzc_v1::RecentBlock* b,
    const rrzc_window_credit::Retention* f,const rrzc_v1::RecentReport& r,
    const rrzc_deadline::History& history,uint64_t horizon,Evidence* evidence=nullptr) {
  const auto g=b?history.Check(r,*b,horizon):rrzc_v1::GrowthEvidence{};
  return EstimateEvidence(c,b,f,r.threshold,horizon,g,evidence);
}
}  // 时间一致性实验核结束。
