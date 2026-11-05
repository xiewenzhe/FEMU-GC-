// 独立空间方案核：默认不接线；比较满足同一当前空间缺口的完整复制成本。
#pragma once
#include "rrzc_direct_selector.h"
#include <string>

namespace rrzc_space_plan {
struct Need {
  bool known=false;
  uint64_t free=0, non_free=0, waiters=0, target=0, bytes=0;
};
inline Need Demand(uint64_t free,uint64_t non_free,uint64_t waiters,unsigned percent=20) {
  Need n;n.free=free;n.non_free=non_free;n.waiters=waiters;
  const __uint128_t total=__uint128_t(free)+non_free;
  if(!total || percent>100 || total>UINT64_MAX)return n;
  n.known=true;n.target=uint64_t((total*percent+99)/100);
  n.bytes=n.target>free ? n.target-free : 0;
  // 聚合空闲字节不等于可分配空Zone；实际等待时必须继续提供空间进展。
  if(waiters && !n.bytes)n.bytes=1;
  return n;
}
struct Input {
  rrzc_direct::Row row;
  uint64_t rr_space=0;
};
using Wide=__uint128_t;
inline std::string Decimal(Wide n) {
  if(!n)return "0";
  std::string s;while(n){s.push_back(char('0'+n%10));n/=10;}
  std::reverse(s.begin(),s.end());return s;
}
struct Plan {
  uint64_t first=0;
  uint64_t first_copy=0,first_reclaim=0;
  bool sufficient=false;
  Wide copy=0, future_rr_copy=0, reclaimed=0, future_rr_space=0;
  std::vector<uint64_t> zones;
  Wide Total() const {return copy+future_rr_copy;}
};
struct Decision {
  bool found=false, progress_only=false;
  uint64_t zone=0;
  std::vector<Plan> plans;
  const char* reason="空间已满足且无分配等待，本轮不选区";
};
inline bool CurrentLess(const Input* a,const Input* b) {
  const auto& x=a->row;const auto& y=b->row;
  const Wide left=Wide(x.candidate.valid)*y.cost.reclaimed;
  const Wide right=Wide(y.candidate.valid)*x.cost.reclaimed;
  if(left!=right)return left<right;
  if(x.candidate.valid!=y.candidate.valid)return x.candidate.valid<y.candidate.valid;
  return x.candidate.zone<y.candidate.zone;
}
inline bool Better(const Plan& a,const Plan& b,bool first_cost=false) {
  if(a.Total()!=b.Total())return a.Total()<b.Total();
  if(a.copy!=b.copy)return a.copy<b.copy;
  if(a.zones.size()!=b.zones.size())return a.zones.size()<b.zones.size();
  // 第二版只修正同成本方案的执行顺序：实际仅搬首区，不能按编号先搬更贵的区。
  // 第一版默认保留，历史实验不能被新规则默默重解释。
  if(first_cost && a.first_copy!=b.first_copy)return a.first_copy<b.first_copy;
  if(first_cost && a.first_reclaim!=b.first_reclaim)return a.first_reclaim>b.first_reclaim;
  if(a.reclaimed+a.future_rr_space!=b.reclaimed+b.future_rr_space)
    return a.reclaimed+a.future_rr_space>b.reclaimed+b.future_rr_space;
  return a.first<b.first;
}
inline Decision Select(const std::vector<Input>& inputs,const Need& need,bool first_cost=false) {
  Decision out;
  if(need.known && !need.bytes)return out;
  std::vector<const Input*> pool;
  for(const auto& x:inputs) {
    const auto& r=x.row;
    if(r.safe && r.candidate.valid && r.candidate.valid<r.candidate.capacity &&
        r.cost.reclaimed==r.candidate.capacity-r.candidate.valid &&
        r.cost.credit<=r.candidate.valid)pool.push_back(&x);
  }
  std::sort(pool.begin(),pool.end(),CurrentLess);
  // 每个候选都独立作为首区；补充区使用当前复制/回收比近似，不预知未来删除。
  for(const auto* first:pool) {
    Plan p;p.first=first->row.candidate.zone;p.first_copy=first->row.candidate.valid;
    p.first_reclaim=first->row.cost.reclaimed;
    auto add=[&](const Input* x){p.zones.push_back(x->row.candidate.zone);
      p.copy+=x->row.candidate.valid;p.reclaimed+=x->row.cost.reclaimed;};
    add(first);
    for(const auto* x:pool) {
      if(p.reclaimed>=need.bytes)break;
      if(x!=first)add(x);
    }
    p.sufficient=need.known && p.reclaimed>=need.bytes;
    for(const auto* x:pool)
      if(std::find(p.zones.begin(),p.zones.end(),x->row.candidate.zone)==p.zones.end()) {
        p.future_rr_copy+=x->row.cost.credit;p.future_rr_space+=x->rr_space;
      }
    out.plans.push_back(p);
  }
  const Plan* best=nullptr;
  for(const auto& p:out.plans)if(p.sufficient && (!best || Better(p,*best,first_cost)))best=&p;
  if(best){out.found=true;out.zone=best->first;
    out.reason="满足同一当前空间缺口的预计总复制最小；仅执行首区，后续重新评估";return out;}
  // 统计未知或候选不足时不伪称收益优化，也不停止有需要的空间回收。
  const Input* progress=nullptr;
  for(const auto* x:pool)if(!progress || x->row.cost.reclaimed>progress->row.cost.reclaimed ||
      (x->row.cost.reclaimed==progress->row.cost.reclaimed && CurrentLess(x,progress)))progress=x;
  if(progress){out.found=true;out.progress_only=true;out.zone=progress->row.candidate.zone;}
  out.reason=pool.empty()?"没有通过公共安全复核的候选":
      "空间统计未知或安全候选不足以补足缺口，仅执行最大空间进展";
  return out;
}
}  // 当前空间需求及方案成本核结束。
