// 单位实际回收空间的净复制评分；不规划或抵扣尚未执行的补充Zone。
#pragma once
#include "rrzc_deadline_selector.h"

namespace rrzc_unit_cost {
struct Row {
  uint64_t zone=0,valid=0,reclaimed=0,credit=0,net=0;
};
struct Decision {
  bool found=false;
  uint64_t zone=0;
  std::vector<Row> rows;
};
inline bool Less(const Row& a,const Row& b) {
  const __uint128_t x=__uint128_t(a.net)*b.reclaimed;
  const __uint128_t y=__uint128_t(b.net)*a.reclaimed;
  if(x!=y)return x<y;
  if(a.valid!=b.valid)return a.valid<b.valid;
  return a.zone<b.zone;
}
inline Decision Select(const std::vector<rrzc_deadline::Input>& inputs) {
  Decision out;
  for(const auto& input:inputs) {
    const auto& r=input.row;const auto& c=r.candidate;
    if(!r.safe || !c.valid || c.valid>=c.capacity ||
        r.cost.reclaimed!=c.capacity-c.valid || r.cost.credit>c.valid)continue;
    // 未知风险不抵扣；保留原估计，不额外引入权重、冷却期或Greedy改选门槛。
    const uint64_t credit=r.cost.risk_known?r.cost.credit:0;
    out.rows.push_back({c.zone,c.valid,r.cost.reclaimed,credit,c.valid-credit});
  }
  std::sort(out.rows.begin(),out.rows.end(),Less);
  if(!out.rows.empty()){out.found=true;out.zone=out.rows.front().zone;}
  return out;
}
}  // 单位回收净复制评分结束。
