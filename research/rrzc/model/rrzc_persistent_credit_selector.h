// 持续读修正版：复用窗口模型的空间成本与留存，只修正读突发后的ETA。
// 默认不接入旧路径；不改RR、GC、候选资格或物理块采样协议。
#pragma once
#include "rrzc_window_credit_selector.h"

namespace rrzc_persistent_credit {
using rrzc_v1::kSecond;
using Retention=rrzc_window_credit::Retention;
using Row=rrzc_direct::Row;
struct Evidence {
  rrzc_window_credit::Evidence window;
  rrzc_v1::GrowthEvidence growth;
  uint64_t average_eta=UINT64_MAX, recent_eta=UINT64_MAX, original_credit=0;
  const char* reason="统计或参数无效";
};

// 纯计算入口的growth须已通过同块端点核验；线上仅由下方Estimate调用。
inline rrzc_direct::Cost EstimateEvidence(const rrzc_simple::Candidate& c,
    const rrzc_v1::RecentBlock* b,const Retention* f,uint64_t threshold,
    uint64_t horizon,const rrzc_v1::GrowthEvidence& growth,Evidence* evidence=nullptr) {
  Evidence local;auto& proof=evidence ? *evidence : local;proof={};proof.growth=growth;
  auto out=rrzc_window_credit::Estimate(c,b,f,threshold,horizon,&proof.window);
  proof.average_eta=out.eta;proof.original_credit=out.credit;proof.reason=proof.window.reason;
  // 失效统计不修补；已经达到阈值的真实需求仍交给原安全路径。
  if (!b || !out.risk_known || b->rc>=threshold || !proof.window.read_span) return out;
  if (!growth.known || growth.interval_ns<rrzc_v1::kGrowthMinNs ||
      growth.interval_ns>rrzc_v1::kGrowthMaxNs || growth.delta>b->rc ||
      growth.interval_ns>b->snapshot_ns-b->epoch_start_ns ||
      (growth.interval_ns<=proof.window.read_span && growth.delta>b->delta10)) {
    out.risk_known=false;out.eta=UINT64_MAX;out.retained=out.credit=0;out.net=c.valid;
    proof.reason="近期同块持续性未知，不借用旧突发给予RR抵扣";return out;
  }
  proof.recent_eta=rrzc_window_credit::Eta(threshold-b->rc,growth.interval_ns,growth.delta);
  out.eta=std::max(proof.average_eta,proof.recent_eta);
  out.retained=out.credit=0;out.net=c.valid;
  if (out.eta==UINT64_MAX) {
    proof.reason="最近同块窗口无增长，累计高RC不等于近期会RR";return out;
  }
  // 原窗口非零才表示原留存及代际条件通过，避免重复放宽旧验证。
  if (!proof.window.window || !f) {
    proof.reason="留存或收益支撑窗口未知，不推定全部存活";return out;
  }
  auto loss=[&](uint64_t bytes,uint64_t span) {
    return std::min<__uint128_t>(c.valid,(__uint128_t(bytes)*out.eta+span-1)/span);
  };
  out.retained=c.valid-uint64_t(std::max(loss(f->short_loss,f->short_span),loss(f->long_loss,f->long_span)));
  const uint64_t window=proof.window.window;
  if (out.eta>=window) {
    proof.reason="持续读ETA超出原可支撑窗口，不预支RR收益";return out;
  }
  // 不增加任意权重，不把短采样间隔直接当作预测时限；沿用原窗口折减。
  out.credit=uint64_t(__uint128_t(out.retained)*(window-out.eta)/(__uint128_t(window)+out.eta));
  out.net=c.valid-out.credit;
  proof.reason=out.retained ? "近期与十秒速率共同支持窗口内收益" : "预计RR前有效数据已退役";
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
}  // 持续读收益实验命名空间结束。
