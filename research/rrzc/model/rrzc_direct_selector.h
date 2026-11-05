// 统一净成本选区：不先选Greedy、不设置相对预算；未知风险仅不给抵扣。
#pragma once
#include "rrzc_retention_selector.h"
#include "rrzc_guarded_selector.h"

namespace rrzc_direct {
using rrzc_v1::kSecond;
inline bool SpareFits(uint64_t source,const rrzc_guard::Bounds& bounds,uint64_t valid,
                     uint64_t capacity,const rrzc_cost::ReserveObservation& before,
                     const rrzc_cost::ReserveObservation& after) {
  // 替补属于迁移池，不能用普通Acquire探测；仅认可两次稳定的真实全空替补。
  return bounds.Complete(valid) && capacity && source!=after.zone &&
      rrzc_guard::StableReserve(before,after) && !after.valid &&
      after.wp==after.zone && after.remaining==capacity && bounds.upper<=after.remaining;
}
struct Cost {
  uint64_t eta=UINT64_MAX,retained=0,credit=0,net=0,reclaimed=0;
  bool risk_known=false;
};
inline Cost Estimate(const rrzc_simple::Candidate& c,const rrzc_v1::RecentBlock* b,
                     const rrzc_retention::Forecast* f,uint64_t threshold,uint64_t horizon) {
  Cost out;out.net=c.valid;out.reclaimed=c.capacity-c.valid;
  if (!b || !threshold || horizon!=10*kSecond || b->zone!=c.zone ||
      !(b->flags & rrzc_v1::kEnabled) || (b->flags & rrzc_v1::kLoss)) return out;
  // 使用真实同块十秒窗口；去掉一秒增长门槛和多尺度取最大ETA的重复保守筛选。
  if (b->rc>=threshold) {out.risk_known=true;out.eta=0;}
  else if (b->flags & rrzc_v1::kValid10) {
    out.risk_known=true;
    if (b->delta10) {
      const __uint128_t n=__uint128_t(threshold-b->rc)*10*kSecond;
      const auto t=(n+b->delta10-1)/b->delta10;
      out.eta=t<UINT64_MAX ? uint64_t(t) : UINT64_MAX;
    }
  }
  if (!out.risk_known || out.eta>horizon) return out;
  // 已达阈值时当前有效量就是当前复制量，不要求额外历史猜测。
  if (!out.eta) out.retained=c.valid;
  else {
    if (!f || !f->known || f->interval10<10*kSecond || f->interval10>12*kSecond ||
        f->interval30<30*kSecond || f->interval30>32*kSecond || f->loss10>f->loss30) return out;
    auto loss=[&](uint64_t bytes,uint64_t span) {
      return std::min<__uint128_t>(c.valid,(__uint128_t(bytes)*out.eta+span-1)/span);
    };
    out.retained=c.valid-uint64_t(std::max(loss(f->loss10,f->interval10),loss(f->loss30,f->interval30)));
  }
  // 十秒是现有维护周期内的收益界限，不再把同一ETA线性折扣第二次。
  out.credit=out.retained;out.net=c.valid-out.credit;
  return out;
}
struct Row {rrzc_simple::Candidate candidate;Cost cost;bool safe=false;};
inline bool Less(const Row& a,const Row& b) {
  const auto x=__uint128_t(a.cost.net)*b.cost.reclaimed;
  const auto y=__uint128_t(b.cost.net)*a.cost.reclaimed;
  if (x!=y) return x<y;
  if (a.candidate.valid!=b.candidate.valid) return a.candidate.valid<b.candidate.valid;
  return a.candidate.zone<b.candidate.zone;
}
inline std::vector<Row> Rank(const std::vector<rrzc_simple::Candidate>& candidates,
    const std::map<uint64_t,rrzc_v1::RecentBlock>& blocks,
    const std::map<uint64_t,rrzc_retention::Forecast>& forecasts,
    uint64_t threshold,uint64_t reclaim,uint64_t horizon) {
  std::vector<Row> rows;
  for (const auto& c:candidates) {
    if (!rrzc_simple::Eligible(c,reclaim) || c.valid>=c.capacity) continue;
    auto b=blocks.find(c.zone);auto f=forecasts.find(c.zone);
    rows.push_back({c,Estimate(c,b==blocks.end()?nullptr:&b->second,
        f==forecasts.end()?nullptr:&f->second,threshold,horizon),false});
  }
  std::sort(rows.begin(),rows.end(),Less);return rows;
}
}  // 统一净成本实验命名空间。
