// 独立边界成本实验核：在同窗空间基准下计入未清候选的延期复制估价。
// 这不是未来GC次数预测；不扩大预测窗口，不用预计RR空间满足当前安全缺口。
#pragma once
#include "rrzc_joint_time_selector.h"

namespace rrzc_terminal {
using rrzc_deadline::Wide;
constexpr uint64_t kScale=rrzc_deadline::kScale;
struct Input {
  rrzc_deadline::Input base;
  bool retention_known=false;
  uint64_t terminal_valid=0;
};
struct Completion {
  uint64_t zone=0,available_space=0,full_copy=0,priced_space=0,priced_copy=0;
};
struct Plan {
  rrzc_deadline::Plan base;
  uint64_t expected_space=0,space_gap=0;
  Wide deferred_copy=0;
  bool priced=false;
  std::vector<Completion> completion;
  Wide Total() const {return base.Total()+deferred_copy;}
};
struct Decision {
  bool found=false,progress_only=false,correction_known=false;
  uint64_t zone=0,reference_space=0;
  std::vector<Plan> plans;
};
// 与原留存估计同一线性下降假设；未知时保留全部当前有效量，不冒充免费回收。
inline uint64_t TerminalValid(const rrzc_simple::Candidate& c,
    const rrzc_window_credit::Retention* f,uint64_t generation,uint64_t window,bool* known) {
  *known=false;
  if(!f || !f->known || f->zone!=c.zone || f->generation!=generation || f->valid!=c.valid ||
      !window || window>10*rrzc_v1::kSecond || !f->short_span || f->short_span>f->long_span ||
      f->long_span<window || f->long_span>32*rrzc_v1::kSecond ||
      f->short_loss>f->long_loss || c.valid>=c.capacity || f->long_loss>c.capacity-c.valid)return c.valid;
  *known=true;
  auto loss=[&](uint64_t bytes,uint64_t span) {
    return std::min<Wide>(c.valid,(Wide(bytes)*window+span-1)/span);
  };
  return c.valid-uint64_t(std::max(loss(f->short_loss,f->short_span),loss(f->long_loss,f->long_span)));
}
inline bool Better(const Plan& a,const Plan& b) {
  if(a.Total()!=b.Total())return a.Total()<b.Total();
  // 只在同边界估价时减少强制RR；不把RR改成ZC的字节重新宣称为节省。
  if(a.base.rr_copy!=b.base.rr_copy)return a.base.rr_copy<b.base.rr_copy;
  if(a.base.first_copy!=b.base.first_copy)return a.base.first_copy<b.base.first_copy;
  if(a.base.zones.size()!=b.base.zones.size())return a.base.zones.size()<b.base.zones.size();
  return a.base.first<b.base.first;
}
inline Decision Select(const std::vector<Input>& raw,const rrzc_space_plan::Need& need) {
  std::vector<rrzc_deadline::Input> original;
  for(const auto& x:raw)original.push_back(x.base);
  const auto previous=rrzc_deadline::Select(original,need);
  Decision out;out.found=previous.found;out.zone=previous.zone;out.progress_only=previous.progress_only;
  if(!need.known || !need.bytes || !previous.found || previous.progress_only)return out;
  std::map<uint64_t,const Input*> pool;Wide total_capacity=0;
  for(const auto& x:raw) {
    const auto& r=x.base.row;const auto& c=r.candidate;
    if(!r.safe || !c.valid || c.valid>=c.capacity || x.base.probability>kScale ||
        r.cost.credit>c.valid || r.cost.reclaimed!=c.capacity-c.valid)continue;
    // 新核的空间互斥分支必须与已记录的同窗概率和条件有效量一致。
    if(r.cost.retained>c.valid || r.cost.credit!=Wide(r.cost.retained)*x.base.probability/kScale ||
        x.base.rr_space!=Wide(c.capacity-r.cost.retained)*x.base.probability/kScale ||
        (x.retention_known && x.terminal_valid>c.valid) || !pool.emplace(c.zone,&x).second)return out;
    total_capacity+=c.capacity;
  }
  if(total_capacity>UINT64_MAX)return out;
  for(const auto& old:previous.plans) {
    Plan p;p.base=old;Wide space=0;
    for(const auto& entry:pool) {
      const auto& x=*entry.second;const auto& c=x.base.row.candidate;
      if(c.zone==old.first){space+=c.capacity-c.valid;continue;}
      const bool member=std::find(old.zones.begin(),old.zones.end(),c.zone)!=old.zones.end();
      // 已计划补充ZC与RR互斥：同一个源区只能回收一次，不能静态回收加全额RR空间。
      if(member)space+=Wide(c.capacity-c.valid)*(kScale-x.base.probability)/kScale;
      space+=x.base.rr_space;
    }
    p.expected_space=uint64_t(space);
    if(old.sufficient)out.reference_space=std::max(out.reference_space,p.expected_space);
    out.plans.push_back(std::move(p));
  }
  for(auto& p:out.plans) {
    if(!p.base.sufficient)continue;
    p.space_gap=out.reference_space-p.expected_space;
    std::vector<Completion> available;
    for(const auto& entry:pool) {
      const auto& x=*entry.second;const auto& c=x.base.row.candidate;
      if(std::find(p.base.zones.begin(),p.base.zones.end(),c.zone)!=p.base.zones.end())continue;
      const auto valid=x.retention_known?x.terminal_valid:c.valid;
      const auto non_rr=kScale-x.base.probability;
      Completion tail;tail.zone=c.zone;
      tail.available_space=uint64_t(Wide(c.capacity-valid)*non_rr/kScale);
      tail.full_copy=uint64_t((Wide(valid)*non_rr+kScale-1)/kScale);
      if(tail.available_space)available.push_back(tail);
    }
    std::sort(available.begin(),available.end(),[](const Completion& a,const Completion& b) {
      const Wide left=Wide(a.full_copy)*b.available_space,right=Wide(b.full_copy)*a.available_space;
      return left!=right?left<right:a.zone<b.zone;
    });
    uint64_t remaining=p.space_gap;
    for(auto tail:available) {
      if(!remaining)break;
      tail.priced_space=std::min(remaining,tail.available_space);
      // 最后一个区允许按空间份额计价：这是分数松弛的边界估价，不声称能部分Reset。
      tail.priced_copy=uint64_t((Wide(tail.full_copy)*tail.priced_space+tail.available_space-1)/tail.available_space);
      remaining-=tail.priced_space;p.deferred_copy+=tail.priced_copy;p.completion.push_back(tail);
    }
    p.priced=!remaining;
    // 任一可行方案无法定价时整轮保留旧选择，避免只给部分候选收费造成偏置。
    if(!p.priced)return out;
  }
  out.correction_known=true;const Plan* best=nullptr;
  for(const auto& p:out.plans)if(p.base.sufficient && (!best || Better(p,*best)))best=&p;
  if(best)out.zone=best->base.first;
  return out;
}
}  // 同窗空间边界估价核结束。
