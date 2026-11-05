// 首区净成本原型：不安排未执行补区；热文件折扣为调度效用，不是RR概率。
#pragma once
#include "rrzc_all_blocks.h"
#include "rrzc_unit_cost_selector.h"

namespace rrzc_first_action {
constexpr uint64_t kScaleCap=30*rrzc_v1::kSecond;
struct File {uint64_t id=0,bytes=0;bool hot=false;};
struct Seen {uint64_t first=0,last=0,minimum=0;};
// 逻辑文件身份在搬迁后保持；消失、字节量改变或时钟倒退后重新积累存活证据。
class Files {
 public:
  void Observe(uint64_t now,const std::vector<File>& files) {
    std::map<uint64_t,Seen> next;
    for(const auto& f:files) {
      auto old=seen_.find(f.id);Seen s{now,now,f.bytes};
      if(old!=seen_.end() && now>old->second.last && f.bytes==old->second.minimum)
        s={old->second.first,now,f.bytes};
      if(!next.emplace(f.id,s).second)throw std::invalid_argument("首区文件身份重复");
    }
    seen_=std::move(next);
  }
  uint64_t Span(uint64_t id,uint64_t bytes,uint64_t now) const {
    auto i=seen_.find(id);
    if(i==seen_.end() || i->second.last!=now || i->second.minimum!=bytes || now<i->second.first)return 0;
    return now-i->second.first;
  }
 private:std::map<uint64_t,Seen> seen_;
};
struct Risk {
  bool known=false;uint64_t eta=UINT64_MAX;uint32_t block=0;
  uint64_t generation=0,rc=0,delta=0,interval=0,delta10=0,age=0;
};
inline Risk Predict(const rrzc_simple::Candidate& c,const rrzc_all_blocks::Report* report,
                    const rrzc_all_blocks::History* history) {
  Risk result;
  if(!report || !history || report->blocks.empty() || !report->threshold ||
      report->blocks.size()!=report->blocks_per_zone || report->blocks[0].zone!=c.zone)return result;
  for(const auto& b:report->blocks) {
    const auto basic=rrzc_window_credit::Estimate(c,&b,nullptr,report->threshold,10*rrzc_v1::kSecond);
    if(!basic.risk_known)continue;
    const auto growth=history->Check(*report,b,300*rrzc_v1::kSecond);
    // 只借用同块历史的身份和速率，不让预测区间改变原2～4秒取样规则。
    if(!growth.known)continue;
    uint64_t eta=UINT64_MAX;
    if(b.rc>=report->threshold)eta=0;
    else if(growth.delta && basic.eta!=UINT64_MAX)
      eta=std::max(basic.eta,rrzc_window_credit::Eta(report->threshold-b.rc,growth.interval_ns,growth.delta));
    if(!result.known || eta<result.eta || (eta==result.eta && b.block_id<result.block))
      result={true,eta,b.block_id,b.generation,b.rc,growth.delta,growth.interval_ns,
              b.delta10,b.snapshot_ns-b.epoch_start_ns};
  }
  return result;
}
inline uint64_t Credit(uint64_t bytes,uint64_t observed,uint64_t eta) {
  if(observed<2*rrzc_v1::kSecond || eta==UINT64_MAX)return 0;
  const auto scale=std::min(observed,kScaleCap);
  return uint64_t(__uint128_t(bytes)*scale/(__uint128_t(scale)+eta));
}
struct Input {rrzc_simple::Candidate candidate;bool safe=false;Risk risk;uint64_t credit=0;};
inline rrzc_unit_cost::Decision Select(const std::vector<Input>& rows) {
  std::vector<rrzc_deadline::Input> inputs;
  for(const auto& x:rows) {
    rrzc_deadline::Input r;r.row.candidate=x.candidate;r.row.safe=x.safe;
    r.row.cost.risk_known=x.risk.known;r.row.cost.credit=x.credit;
    r.row.cost.reclaimed=x.candidate.valid<x.candidate.capacity?x.candidate.capacity-x.candidate.valid:0;
    inputs.push_back(r);
  }
  return rrzc_unit_cost::Select(inputs);
}
}  // 未知风险/非热文件不抵扣，所有共同安全候选直接评分。
