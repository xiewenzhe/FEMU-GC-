// 边界核第二版：统一将期望复制向上取整，防止一字节取整差驱动整区改选。
// 第一版原样保留以复现冻结实验；第二版接线必须显式启用并独立验收。
#pragma once
#include "rrzc_terminal_selector.h"

namespace rrzc_terminal_v2 {
using rrzc_terminal::Input;
using rrzc_terminal::Wide;
struct Plan {
  rrzc_terminal::Plan base;
  uint64_t rr_rounding=0;
  Wide Total() const {return base.Total()+rr_rounding;}
};
struct Decision {
  bool found=false,progress_only=false,correction_known=false;
  uint64_t zone=0,reference_space=0;
  std::vector<Plan> plans;
};
inline bool Better(const Plan& a,const Plan& b) {
  if(a.Total()!=b.Total())return a.Total()<b.Total();
  const auto& x=a.base.base;const auto& y=b.base.base;
  if(x.rr_copy+a.rr_rounding!=y.rr_copy+b.rr_rounding)
    return x.rr_copy+a.rr_rounding<y.rr_copy+b.rr_rounding;
  if(x.first_copy!=y.first_copy)return x.first_copy<y.first_copy;
  if(x.zones.size()!=y.zones.size())return x.zones.size()<y.zones.size();
  return x.first<y.first;
}
inline Decision Select(const std::vector<Input>& rows,const rrzc_space_plan::Need& need) {
  const auto original=rrzc_terminal::Select(rows,need);
  Decision out;out.found=original.found;out.progress_only=original.progress_only;
  out.correction_known=original.correction_known;out.zone=original.zone;out.reference_space=original.reference_space;
  for(const auto& p:original.plans) {
    Plan revised;revised.base=p;
    if(original.correction_known)for(const auto& r:rows) {
      const auto& b=r.base;const auto& c=b.row.candidate;
      if(b.row.safe && c.zone!=p.base.first && c.valid && c.valid<c.capacity &&
          b.probability<=rrzc_terminal::kScale && b.row.cost.credit<=c.valid &&
          b.row.cost.reclaimed==c.capacity-c.valid &&
          Wide(b.row.cost.retained)*b.probability%rrzc_terminal::kScale)++revised.rr_rounding;
    }
    out.plans.push_back(std::move(revised));
  }
  if(original.correction_known) {
    const Plan* best=nullptr;
    for(const auto& p:out.plans)if(p.base.base.sufficient && (!best || Better(p,*best)))best=&p;
    if(best)out.zone=best->base.base.first;
  }
  return out;
}
}  // 复制取整一致的边界估价核结束。
