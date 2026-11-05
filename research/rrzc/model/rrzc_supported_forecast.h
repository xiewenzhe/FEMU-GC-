// 独立预测原型：相邻不重叠读窗口、有限外推及比例留存；尚未接入客机。
#pragma once
#include "rrzc_joint_time_selector.h"

namespace rrzc_supported {
using Wide=__uint128_t;
constexpr uint64_t kSecond=rrzc_v1::kSecond;
struct Growth {
  bool known=false;
  uint64_t first_delta=0,first_span=0,second_delta=0,second_span=0;
  uint64_t Span() const {return first_span+second_span;}
  const char* reason="缺少两个相邻且不重叠的同块区间";
};
enum class Scope { EvidenceOnly, CompatibleRates };
// 给定两段总读取数，同速率假设下第一段计数服从二项分布。
// 使用保守双尾检验；检验值不是未来RR概率，也不证明未来读热度恒定。
inline rrzc_expected_rr::Probability Compatibility(const Growth& g) {
  if(!g.known || !g.first_span || !g.second_span ||
      Wide(g.first_span)+g.second_span>UINT64_MAX ||
      Wide(g.first_delta)+g.second_delta>rrzc_expected_rr::kMaxTerms)return {};
  const uint64_t count=g.first_delta+g.second_delta;
  if(!count)return {true,rrzc_joint_time::kScale};
  const long double p=static_cast<long double>(g.first_span)/g.Span();
  const uint64_t mode=std::min(count,uint64_t((count+1)*p));
  long double sum=0,lower=0,upper=0;
  auto add=[&](uint64_t n,long double term) {
    sum+=term;if(n<=g.first_delta)lower+=term;if(n>=g.first_delta)upper+=term;
  };
  long double term=1;add(mode,term);
  for(uint64_t n=mode;n;--n) {
    term*=static_cast<long double>(n)*(1-p)/((count-n+1)*p);add(n-1,term);
  }
  term=1;
  for(uint64_t n=mode;n<count;++n) {
    term*=static_cast<long double>(count-n)*p/((n+1)*(1-p));add(n+1,term);
  }
  if(!(sum>0) || !std::isfinite(sum))return {};
  const long double value=std::min(1.L,2*std::min(lower,upper)/sum);
  return {true,uint64_t(value*rrzc_joint_time::kScale)};
}
inline Growth Check(const rrzc_v1::RecentReport& current,const rrzc_v1::RecentBlock& b,
    const std::deque<rrzc_v1::RecentReport>& history) {
  Growth out;
  if(!current.threshold || !current.blocks_per_zone || !(current.session_hi || current.session_lo) ||
      !(b.flags&rrzc_v1::kEnabled) || (b.flags&rrzc_v1::kLoss) || (b.flags&~uint32_t(15)) ||
      b.block_id>=current.blocks_per_zone || b.epoch_start_ns>b.snapshot_ns)return out;
  uint64_t last_time=b.snapshot_ns,last_rc=b.rc,middle_time=0,middle_rc=0;
  for(auto frame=history.rbegin();frame!=history.rend();++frame) {
    auto it=std::find_if(frame->zones.begin(),frame->zones.end(),
        [&](const rrzc_v1::RecentBlock& p){return p.zone==b.zone;});
    if(it==frame->zones.end()){out.reason="中间采样缺失，不跨越拼接";return out;}
    const auto& p=*it;
    if(frame->session_hi!=current.session_hi || frame->session_lo!=current.session_lo ||
        frame->threshold!=current.threshold || frame->blocks_per_zone!=current.blocks_per_zone ||
        p.generation!=b.generation || p.epoch_start_ns!=b.epoch_start_ns || p.zone_index!=b.zone_index ||
        p.block_id!=b.block_id || p.ch!=b.ch || p.lun!=b.lun || p.plane!=b.plane || p.block!=b.block ||
        !(p.flags&rrzc_v1::kEnabled) || (p.flags&rrzc_v1::kLoss) || (p.flags&~uint32_t(15))) {
      out.reason="会话、代际或最大块身份变化，不做最大值差分";return out;
    }
    if(p.snapshot_ns>=last_time || p.rc>last_rc || p.snapshot_ns<p.epoch_start_ns) {
      out.reason="同块时间或RC不单调";return out;
    }
    last_time=p.snapshot_ns;last_rc=p.rc;
    const uint64_t span=b.snapshot_ns-p.snapshot_ns;
    if(span>4*kSecond){out.reason="超出原四秒近期证据范围";return out;}
    // 沿用已有半秒最小观察尺度；很短的当前前缀不单独作为零读结论。
    if(!middle_time) {
      if(span<rrzc_v1::kGrowthMinNs)continue;
      middle_time=p.snapshot_ns;middle_rc=p.rc;
      out.first_delta=b.rc-p.rc;out.first_span=span;continue;
    }
    if(span<2*kSecond || middle_time-p.snapshot_ns<rrzc_v1::kGrowthMinNs)continue;
    out.second_delta=middle_rc-p.rc;out.second_span=middle_time-p.snapshot_ns;
    out.known=true;out.reason="两个相邻同块区间，包含中间身份与单调性检查";return out;
  }
  return out;
}

// 将观察到的存活比例作为待校准的衰减假设，不再给出有限的全部失效时刻。
inline bool Live(uint64_t valid,const rrzc_window_credit::Retention& f,uint64_t time,uint64_t* result) {
  *result=valid;
  if(!valid || !f.known || !f.short_span || f.short_span>f.long_span ||
      f.long_span>32*kSecond || f.short_loss>f.long_loss)return false;
  long double fraction=1;
  for(auto sample:{std::pair<uint64_t,uint64_t>{f.short_loss,f.short_span},
                    std::pair<uint64_t,uint64_t>{f.long_loss,f.long_span}}) {
    const long double exponent=-std::log1p(static_cast<long double>(sample.first)/valid)*time/sample.second;
    fraction=std::min(fraction,std::exp(exponent));
  }
  if(!std::isfinite(fraction) || fraction<0 || fraction>1)return false;
  *result=uint64_t(std::min(static_cast<long double>(valid),static_cast<long double>(valid)*fraction));
  return true;
}
struct Evidence {
  Growth growth;
  rrzc_window_credit::Evidence window;
  rrzc_expected_rr::Probability probability;
  rrzc_expected_rr::Probability compatibility;
  uint64_t rate_delta=0,rate_span=0,event_window=0,conditional_ns=0;
  const char* reason="统计未知，仅保留当前空间成本";
};
inline rrzc_direct::Cost Estimate(const rrzc_simple::Candidate& c,const rrzc_v1::RecentBlock* b,
    const rrzc_window_credit::Retention* f,const rrzc_v1::RecentReport& report,
    const std::deque<rrzc_v1::RecentReport>& history,uint64_t horizon,Evidence* evidence=nullptr,
    Scope scope=Scope::EvidenceOnly) {
  Evidence local;auto& e=evidence?*evidence:local;e={};
  // 只复用原输入校验与原历史可支撑窗口，下面替换两个未经验证的外推。
  auto out=rrzc_window_credit::Estimate(c,b,f,report.threshold,horizon,&e.window);
  out.retained=out.credit=0;out.net=c.valid;
  if(!b || !out.risk_known || !c.valid)return out;
  if(b->rc>=report.threshold) {
    out.retained=out.credit=c.valid;out.net=0;e.probability={true,rrzc_joint_time::kScale};
    e.reason="已到阈值的当前需求，不当作提前预测收益";return out;
  }
  e.growth=Check(report,*b,history);
  if(!e.growth.known){out.risk_known=false;out.eta=UINT64_MAX;e.reason=e.growth.reason;return out;}
  if(!e.window.read_span || !e.window.window || !f)return out;
  e.rate_delta=b->delta10;e.rate_span=e.window.read_span;
  for(auto sample:{std::pair<uint64_t,uint64_t>{e.growth.first_delta,e.growth.first_span},
                    std::pair<uint64_t,uint64_t>{e.growth.second_delta,e.growth.second_span}}) {
    if(Wide(sample.first)*e.rate_span<Wide(e.rate_delta)*sample.second) {
      e.rate_delta=sample.first;e.rate_span=sample.second;
    }
  }
  out.eta=rrzc_window_credit::Eta(report.threshold-b->rc,e.rate_span,e.rate_delta);
  // 外推不长于两段近期证据的总长度；不是改变GC周期或RR安全阈值。
  e.event_window=std::min(e.window.window,e.growth.Span());
  if(scope==Scope::CompatibleRates) {
    e.compatibility=Compatibility(e.growth);
    // 固定5%兼容性检验口径，不是成本权重；需后续检验误报/漏报及敏感性。
    if(e.compatibility.known && e.compatibility.units>=rrzc_joint_time::kScale/20)
      e.event_window=e.window.window;
  }
  if(!e.rate_delta){e.probability={true,0};e.reason="至少一个独立区间无增长，近期收益未知或不足";return out;}
  const long double mean=static_cast<long double>(e.rate_delta)*e.event_window/e.rate_span;
  e.probability=rrzc_expected_rr::ThresholdProbability(report.threshold-b->rc,mean);
  if(!e.probability.known || !e.probability.units)return out;
  if(!rrzc_joint_time::ConditionalTime(report.threshold-b->rc,mean,e.event_window,&e.conditional_ns) ||
      !Live(c.valid,*f,e.conditional_ns,&out.retained) || !out.retained) {
    e.probability={};e.reason="条件留存无法可靠计价，不减免补充ZC成本";return out;
  }
  out.credit=uint64_t(Wide(out.retained)*e.probability.units/rrzc_joint_time::kScale);
  out.net=c.valid-out.credit;
  e.reason="非重叠同块区间及比例留存原型；泊松与衰减均未校准";return out;
}
}  // 有限证据支持的独立预测核结束。
