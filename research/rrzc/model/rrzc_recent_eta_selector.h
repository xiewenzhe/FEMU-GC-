// 独立ETA实验：同块最近0.5～2秒的实测增长替代十秒平均速率。
// 保持十秒收益界限、留存估计、候选资格及直接成本排序，不预先选Greedy。
#pragma once
#include "rrzc_direct_selector.h"

namespace rrzc_recent_eta {
using rrzc_v1::kSecond;
using Row=rrzc_direct::Row;
inline rrzc_direct::Cost Estimate(const rrzc_simple::Candidate& c,
    const rrzc_v1::RecentBlock* b,const rrzc_retention::Forecast* f,
    const rrzc_v1::RecentReport& report,const rrzc_v1::GrowthHistory& history,
    uint64_t horizon,rrzc_v1::GrowthEvidence* evidence=nullptr) {
  rrzc_direct::Cost out;out.net=c.valid;out.reclaimed=c.capacity-c.valid;
  if(!b || !report.threshold || horizon!=10*kSecond || b->zone!=c.zone ||
      !(b->flags&rrzc_v1::kEnabled) || (b->flags&rrzc_v1::kLoss))return out;
  // 阈值已达到时不因缺少历史否定真实风险；RR执行路径不受此实验控制。
  if(b->rc>=report.threshold){out.risk_known=true;out.eta=0;}
  else {
    const auto growth=history.Check(report,*b,horizon,true);
    if(evidence)*evidence=growth;
    if(!growth.known)return out;
    out.risk_known=true;
    if(growth.delta)out.eta=growth.predicted_ns;
  }
  if(!out.risk_known || out.eta>horizon)return out;
  if(!out.eta)out.retained=c.valid;
  else {
    if(!f || !f->known || f->interval10<10*kSecond || f->interval10>12*kSecond ||
        f->interval30<30*kSecond || f->interval30>32*kSecond || f->loss10>f->loss30)return out;
    auto loss=[&](uint64_t bytes,uint64_t span){
      return std::min<__uint128_t>(c.valid,(__uint128_t(bytes)*out.eta+span-1)/span);
    };
    out.retained=c.valid-uint64_t(std::max(loss(f->loss10,f->interval10),loss(f->loss30,f->interval30)));
  }
  out.credit=out.retained;out.net=c.valid-out.credit;return out;
}
inline std::vector<Row> Rank(const std::vector<rrzc_simple::Candidate>& candidates,
    const std::map<uint64_t,rrzc_v1::RecentBlock>& blocks,
    const std::map<uint64_t,rrzc_retention::Forecast>& forecasts,
    const rrzc_v1::RecentReport& report,const rrzc_v1::GrowthHistory& history,
    uint64_t reclaim,uint64_t horizon) {
  std::vector<Row> rows;
  for(const auto& c:candidates) {
    if(!rrzc_simple::Eligible(c,reclaim))continue;
    const auto b=blocks.find(c.zone);const auto f=forecasts.find(c.zone);
    rows.push_back({c,Estimate(c,b==blocks.end()?nullptr:&b->second,
        f==forecasts.end()?nullptr:&f->second,report,history,horizon),false});
  }
  std::sort(rows.begin(),rows.end(),rrzc_direct::Less);return rows;
}
}  // 近期ETA实验命名空间结束。
