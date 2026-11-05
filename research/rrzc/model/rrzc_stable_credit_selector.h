// 独立实验核：持续读速率与时间折减收益；不修改旧模型、RR或GC资格。
#pragma once
#include "rrzc_recent_eta_selector.h"

namespace rrzc_stable_credit {
using rrzc_v1::kSecond;
using Row=rrzc_direct::Row;
struct Evidence {
  rrzc_v1::GrowthEvidence growth;
  uint64_t short_eta=UINT64_MAX, average_eta=UINT64_MAX;
  const char* reason="统计或参数无效";
};
inline uint64_t Eta(uint64_t margin,uint64_t span,uint64_t reads) {
  if (!reads) return UINT64_MAX;
  const auto value=(__uint128_t(margin)*span+reads-1)/reads;
  return value>=UINT64_MAX ? UINT64_MAX : uint64_t(value);
}
// 此纯计算入口只接收已通过同块身份检查的增长证据；线上使用下方Estimate。
inline rrzc_direct::Cost EstimateEvidence(const rrzc_simple::Candidate& c,
    const rrzc_v1::RecentBlock* b,const rrzc_retention::Forecast* f,
    uint64_t threshold,uint64_t horizon,const rrzc_v1::GrowthEvidence& growth,
    Evidence* evidence=nullptr) {
  Evidence local;local.growth=growth;
  auto& proof=evidence ? *evidence : local;proof=local;
  rrzc_direct::Cost out;out.net=c.valid;
  if (c.valid>=c.capacity) return out;
  out.reclaimed=c.capacity-c.valid;
  if (!b || b->zone!=c.zone || !threshold || horizon!=10*kSecond ||
      !(b->flags&rrzc_v1::kEnabled) || (b->flags&rrzc_v1::kLoss) ||
      (b->flags&~uint32_t(15)) || b->epoch_start_ns>b->snapshot_ns ||
      b->delta10>b->delta30 || b->delta30>b->rc) return out;
  // 已达阈值仍交由原强制RR路径保护，不允许本模型推迟RR。
  if (b->rc>=threshold) {
    out.risk_known=true;out.eta=0;out.retained=out.credit=c.valid;out.net=0;
    proof.reason="已达到阈值，当前有效量计入待维护成本";return out;
  }
  if (!(b->flags&rrzc_v1::kValid10) ||
      b->snapshot_ns-b->epoch_start_ns<10*kSecond || !growth.known ||
      growth.interval_ns<rrzc_v1::kGrowthMinNs || growth.interval_ns>rrzc_v1::kGrowthMaxNs ||
      growth.delta>b->rc) {
    proof.reason="缺少完整十秒窗口或同块近期证据";return out;
  }
  out.risk_known=true;
  proof.short_eta=Eta(threshold-b->rc,growth.interval_ns,growth.delta);
  proof.average_eta=Eta(threshold-b->rc,10*kSecond,b->delta10);
  // 慢速率决定ETA，既不借用已结束的历史突发，也不将一秒加速全额外推。
  out.eta=std::max(proof.short_eta,proof.average_eta);
  if (out.eta==UINT64_MAX) {proof.reason="近期或十秒窗口没有持续增长";return out;}
  if (!f || !f->known || f->interval10<10*kSecond || f->interval10>12*kSecond ||
      f->interval30<30*kSecond || f->interval30>32*kSecond || f->loss10>f->loss30) {
    proof.reason="留存历史未知，不推定未来全部存活";return out;
  }
  // 继续使用原两窗口较快退役速率；它不能预知尚未出现的突然文件退役。
  auto loss=[&](uint64_t bytes,uint64_t span) {
    return std::min<__uint128_t>(c.valid,(__uint128_t(bytes)*out.eta+span-1)/span);
  };
  out.retained=c.valid-uint64_t(std::max(loss(f->loss10,f->interval10),loss(f->loss30,f->interval30)));
  // H/(H+ETA)是调度时间折减，不是经校准的发生概率；不新增50/50权重。
  // 不再在十秒边界从全额突然跳到零，也不把扩大窗口当作确定节省一次RR。
  out.credit=uint64_t(__uint128_t(out.retained)*horizon/(__uint128_t(horizon)+out.eta));
  out.net=c.valid-out.credit;
  proof.reason=out.retained ? "持续速率、预计留存与时间折减共同计入" : "预计RR前有效数据已退役";
  return out;
}
inline rrzc_direct::Cost Estimate(const rrzc_simple::Candidate& c,
    const rrzc_v1::RecentBlock* b,const rrzc_retention::Forecast* f,
    const rrzc_v1::RecentReport& report,const rrzc_v1::GrowthHistory& history,
    uint64_t horizon,Evidence* evidence=nullptr) {
  const auto growth=b ? history.Check(report,*b,horizon,true) : rrzc_v1::GrowthEvidence{};
  return EstimateEvidence(c,b,f,report.threshold,horizon,growth,evidence);
}
inline std::vector<Row> Rank(const std::vector<rrzc_simple::Candidate>& candidates,
    const std::map<uint64_t,rrzc_v1::RecentBlock>& blocks,
    const std::map<uint64_t,rrzc_retention::Forecast>& forecasts,
    const rrzc_v1::RecentReport& report,const rrzc_v1::GrowthHistory& history,
    uint64_t reclaim,uint64_t horizon) {
  std::vector<Row> rows;
  for (const auto& c:candidates) {
    if (!rrzc_simple::Eligible(c,reclaim)) continue;
    const auto b=blocks.find(c.zone);const auto f=forecasts.find(c.zone);
    rows.push_back({c,Estimate(c,b==blocks.end()?nullptr:&b->second,
        f==forecasts.end()?nullptr:&f->second,report,history,horizon),false});
  }
  std::sort(rows.begin(),rows.end(),rrzc_direct::Less);return rows;
}
}  // 稳定读速率与时间折减实验命名空间结束。
