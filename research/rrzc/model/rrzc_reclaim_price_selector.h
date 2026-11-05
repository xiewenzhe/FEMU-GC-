// 独立V2：取消RR会同时取消其空间回收，收益须扣除当前缺口内的替代复制估价。
#pragma once
#include "rrzc_unit_cost_selector.h"

namespace rrzc_reclaim_price {
struct Evidence {
  uint64_t zone=0,raw_credit=0,rr_space=0,replaced_space=0;
  uint64_t price_copy=0,price_space=0,debit=0,effective_credit=0;
  bool full_space=false;
};
inline rrzc_unit_cost::Decision Select(const std::vector<rrzc_deadline::Input>& inputs,
    const rrzc_space_plan::Need& need,std::vector<Evidence>* evidence=nullptr) {
  if(evidence)evidence->clear();
  auto out=rrzc_unit_cost::Select(inputs);
  if(out.rows.empty())return out;
  // 从全部安全候选估计当前复制/回收的最低单位价格；这里只定价，不预选victim。
  const auto reference=*std::min_element(out.rows.begin(),out.rows.end(),[](const auto& a,const auto& b) {
    const __uint128_t x=__uint128_t(a.valid)*b.reclaimed,y=__uint128_t(b.valid)*a.reclaimed;
    if(x!=y)return x<y;
    if(a.valid!=b.valid)return a.valid<b.valid;
    return a.zone<b.zone;
  });
  for(auto& r:out.rows) {
    const auto it=std::find_if(inputs.begin(),inputs.end(),[&](const auto& x) {
      return x.row.candidate.zone==r.zone;
    });
    Evidence e;e.zone=r.zone;e.raw_credit=r.credit;e.rr_space=it->rr_space;
    e.price_copy=reference.valid;e.price_space=reference.reclaimed;
    // 有真实分配等待时聚合空闲字节不能说明可分配性；不拿零字节缺口免除空间代价。
    e.full_space=!need.known || need.waiters>0;
    e.replaced_space=e.full_space?e.rr_space:std::min(e.rr_space,need.bytes);
    const __uint128_t debit=(__uint128_t(e.replaced_space)*e.price_copy+e.price_space-1)/e.price_space;
    e.debit=uint64_t(std::min<__uint128_t>(e.raw_credit,debit));
    e.effective_credit=e.raw_credit-e.debit;
    r.credit=e.effective_credit;r.net=r.valid-r.credit;
    if(evidence)evidence->push_back(e);
  }
  std::sort(out.rows.begin(),out.rows.end(),rrzc_unit_cost::Less);
  out.zone=out.rows.front().zone;
  return out;
}
}  // 空间替代定价结束；它是局部估价，不是完整放置/未来调度模拟。
