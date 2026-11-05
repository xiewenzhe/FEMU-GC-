// 独立实验核：以窗口内到阈值需求概率替换任意时间折扣；默认不接线。
// 泊松读取是待验证的工作负载假设，不是NAND物理模型或校准后的真实概率。
#pragma once
#include "rrzc_persistent_credit_selector.h"
#include <cmath>

namespace rrzc_expected_rr {
using rrzc_v1::kSecond;
using Retention=rrzc_window_credit::Retention;
using Row=rrzc_direct::Row;
constexpr uint64_t kProbabilityScale=1000000000;
// 仅为计算资源上限；超过时保留候选按空间成本排序，不改设备阈值。
constexpr uint64_t kMaxTerms=100000;
struct Probability { bool known=false;uint64_t units=0; };

inline Probability ThresholdProbability(uint64_t margin,long double mean) {
  if (!margin) return {true,kProbabilityScale};
  if (!std::isfinite(mean) || mean<0 || margin>kMaxTerms) return {};
  if (!mean) return {true,0};
  // 从靠近分界的概率项递推，避免从exp(-均值)开始导致下溢。
  const bool lower=mean>=static_cast<long double>(margin);
  const uint64_t start=lower ? margin-1 : margin;
  long double term=std::exp(-mean+start*std::log(mean)-std::lgamma(start+1.0L));
  long double sum=term;
  uint64_t n=start;
  bool converged=false;
  for (uint64_t i=0;i<kMaxTerms;++i) {
    if (lower && !n) {converged=true;break;}
    const long double ratio=lower ? n/mean : mean/(n+1.0L);
    const long double next=term*ratio;
    // 后续比率单调下降；几何级数界限制截断的绝对误差。
    const long double remainder=ratio<1 ? next/(1-ratio) : 1;
    if (remainder<1e-12L) {
      if (lower) sum+=remainder;
      converged=true;break;
    }
    term=next;sum+=term;
    if (lower) --n;else ++n;
  }
  if (!converged || !std::isfinite(sum)) return {};
  const long double p=std::max(0.0L,std::min(1.0L,lower ? 1-sum : sum));
  // 未到阈值的预测不记录成确定事件；尾概率小于分辨率时仍保留一单位不确定性。
  return {true,std::min(kProbabilityScale-1,static_cast<uint64_t>(p*kProbabilityScale))};
}

struct Evidence {
  rrzc_persistent_credit::Evidence persistence;
  Probability probability;
  uint64_t previous_credit=0;
  const char* reason="统计或参数无效";
};

// 纯核仅接受已经核验同块、同代际的增长；线上使用下方Estimate。
inline rrzc_direct::Cost EstimateEvidence(const rrzc_simple::Candidate& c,
    const rrzc_v1::RecentBlock* b,const Retention* f,uint64_t threshold,
    uint64_t horizon,const rrzc_v1::GrowthEvidence& growth,Evidence* evidence=nullptr) {
  Evidence local;auto& proof=evidence ? *evidence : local;proof={};
  auto out=rrzc_persistent_credit::EstimateEvidence(c,b,f,threshold,horizon,growth,&proof.persistence);
  proof.previous_credit=out.credit;proof.reason=proof.persistence.reason;
  if (!b || !out.risk_known) return out;
  if (b->rc>=threshold) {
    proof.probability={true,kProbabilityScale};
    proof.reason="已到阈值的当前需求，保持原安全维护语义";return out;
  }
  out.credit=0;out.net=c.valid;
  const auto& window=proof.persistence.window;
  if (out.eta==UINT64_MAX || !window.window || !out.retained) return out;
  // 两个真实窗口取较慢读取速率；窗口W仍由读历史、留存历史及原十秒共同限制。
  const long double rate=std::min(
      static_cast<long double>(b->delta10)/window.read_span,
      static_cast<long double>(growth.delta)/growth.interval_ns);
  proof.probability=ThresholdProbability(threshold-b->rc,rate*window.window);
  if (!proof.probability.known) {
    proof.reason="需求概率计算未知，候选仍按空间成本参与";return out;
  }
  // 概率定点化后使用128位整数算字节；不再另外乘时间效用权重。
  out.credit=uint64_t(__uint128_t(out.retained)*proof.probability.units/kProbabilityScale);
  out.net=c.valid-out.credit;
  proof.reason="持续读取泊松假设下的窗口内需求概率乘预计留存；未校准，不预知未来删除";
  return out;
}

inline rrzc_direct::Cost Estimate(const rrzc_simple::Candidate& c,
    const rrzc_v1::RecentBlock* b,const Retention* f,const rrzc_v1::RecentReport& report,
    const rrzc_v1::GrowthHistory& history,uint64_t horizon,Evidence* evidence=nullptr) {
  const auto growth=b ? history.Check(report,*b,horizon,true) : rrzc_v1::GrowthEvidence{};
  return EstimateEvidence(c,b,f,report.threshold,horizon,growth,evidence);
}

inline std::vector<Row> Rank(const std::vector<rrzc_simple::Candidate>& candidates,
    const std::map<uint64_t,rrzc_v1::RecentBlock>& blocks,
    const std::map<uint64_t,Retention>& forecasts,const rrzc_v1::RecentReport& report,
    const rrzc_v1::GrowthHistory& history,uint64_t reclaim,uint64_t horizon) {
  std::vector<Row> rows;
  for (const auto& c:candidates) {
    if (!rrzc_simple::Eligible(c,reclaim)) continue;
    const auto b=blocks.find(c.zone);const auto f=forecasts.find(c.zone);
    rows.push_back({c,Estimate(c,b==blocks.end()?nullptr:&b->second,
        f==forecasts.end()?nullptr:&f->second,report,history,horizon),false});
  }
  std::sort(rows.begin(),rows.end(),rrzc_direct::Less);return rows;
}
}  // 窗口内期望RR成本实验命名空间结束。
