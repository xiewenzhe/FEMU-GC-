// 独立试验核：比较同一当前空间目标，计划内只计一次复制；默认不接入ZenFS。
#pragma once
#include "rrzc_unit_cost_selector.h"
#include <set>

namespace rrzc_maintenance_cover {
using Wide=__uint128_t;
// 原GC使用整数空闲百分比，<=level时仍可能执行。这里只计算跨出检查线的字节目标。
// 不修改GC条件；总量暂按当前观察冻结，实际空间、候选和资源每轮重新核验。
inline rrzc_space_plan::Need ExitNeed(uint64_t free,uint64_t non_free,
                                    uint64_t waiters,unsigned level=20) {
  if(level>=100)return {};
  return rrzc_space_plan::Demand(free,non_free,waiters,level+1);
}
struct Plan {
  uint64_t first=0,first_copy=0,first_reclaim=0;
  bool covered=false;
  Wide copy=0,reclaimed=0,pending_rr=0,deferred_valid=0;
  std::vector<uint64_t> zones;
  Wide Total() const {return copy+pending_rr;}
};
struct Decision {
  bool found=false,invalid=false,space_unknown=false;
  uint64_t zone=0;
  std::vector<Plan> plans;
};
inline bool Better(const Plan& a,const Plan& b) {
  if(a.covered!=b.covered)return a.covered;
  if(a.Total()!=b.Total())return a.Total()<b.Total();
  // 同成本时优先少付当前复制；不为少报RR次数额外支付字节。
  if(a.first_copy!=b.first_copy)return a.first_copy<b.first_copy;
  if(a.first_reclaim!=b.first_reclaim)return a.first_reclaim>b.first_reclaim;
  return a.first<b.first;
}
inline Decision Select(const std::vector<rrzc_deadline::Input>& inputs,
                       const rrzc_space_plan::Need& need) {
  Decision out;
  auto rows=rrzc_unit_cost::Select(inputs).rows;
  std::set<uint64_t> ids;
  for(const auto& r:rows)if(!ids.insert(r.zone).second){out.invalid=true;return out;}
  if(rows.empty())return out;
  // 未知缺口或资源等待不能由字节目标证明满足，不借预测RR支撑改选。
  // 此处仍按当前复制/回收代价产生进展建议，不改变外部安全复核和分配重试。
  out.space_unknown=!need.known || need.waiters || !need.bytes;
  std::sort(rows.begin(),rows.end(),[](const auto& a,const auto& b) {
    const Wide x=Wide(a.valid)*b.reclaimed,y=Wide(b.valid)*a.reclaimed;
    if(x!=y)return x<y;
    if(a.valid!=b.valid)return a.valid<b.valid;
    return a.zone<b.zone;
  });
  if(out.space_unknown) {
    const auto& r=rows.front();Plan p;
    p.first=r.zone;p.first_copy=r.valid;p.first_reclaim=r.reclaimed;
    p.copy=r.valid;p.reclaimed=r.reclaimed;p.zones.push_back(r.zone);
    for(const auto& x:rows)if(x.zone!=r.zone)p.deferred_valid+=x.valid;
    out.plans.push_back(p);out.found=true;out.zone=r.zone;return out;
  }
  // 复用已有独立空间计划的枚举，不另造补区算法；未知RR金额先归零。
  // 全部候选分别作为首区，补区按当前复制/回收比；它是启发式，非全局最优。
  std::vector<rrzc_space_plan::Input> normalized;
  for(const auto& r:rows) {
    const auto original=std::find_if(inputs.begin(),inputs.end(),[&](const auto& x) {
      return x.row.safe && x.row.candidate.zone==r.zone && x.row.candidate.valid==r.valid &&
          x.row.candidate.capacity==r.valid+r.reclaimed && x.row.cost.credit<=r.valid;
    });
    auto row=original->row;row.cost.credit=r.credit;
    normalized.push_back({row,0});
  }
  const auto snapshot=rrzc_space_plan::Select(normalized,need,true);
  for(const auto& source:snapshot.plans) {
    Plan p;p.first=source.first;p.first_copy=source.first_copy;p.first_reclaim=source.first_reclaim;
    p.covered=source.sufficient;p.copy=source.copy;p.reclaimed=source.reclaimed;
    p.pending_rr=source.future_rr_copy;p.zones=source.zones;
    // 只是未纳入计划的源区当前有效量，不是未来总成本上界或预测节省。
    for(const auto& r:rows)
      if(std::find(p.zones.begin(),p.zones.end(),r.zone)==p.zones.end())p.deferred_valid+=r.valid;
    out.plans.push_back(p);
  }
  const auto best=std::min_element(out.plans.begin(),out.plans.end(),Better);
  out.found=true;out.zone=best->first;return out;
}
}  // 当前空间覆盖与计划外RR复制比较结束；执行时只搬首区，其余不是预定victim。
