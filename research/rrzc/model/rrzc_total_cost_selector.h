// 独立实验核：本次真实复制加未选候选的预计RR复制，再按回收空间归一。
// 复用旧预测，不引入新权重、不改变资格、不预先选Greedy，也不自动接线。
#pragma once
#include "rrzc_direct_selector.h"
#include <limits>
#include <set>

namespace rrzc_total {
struct Row {
  rrzc_direct::Row original;
  uint64_t deferred_rr=0,total_copy=0;
};
struct Report {
  bool known=false;
  uint64_t pool_rr=0;
  std::vector<Row> rows;
  const char* reason="尚未验证候选及预计复制账目";
};
inline bool Less(const Row& a,const Row& b) {
  const auto x=__uint128_t(a.total_copy)*b.original.cost.reclaimed;
  const auto y=__uint128_t(b.total_copy)*a.original.cost.reclaimed;
  if(x!=y)return x<y;
  if(a.original.candidate.valid!=b.original.candidate.valid)
    return a.original.candidate.valid<b.original.candidate.valid;
  return a.original.candidate.zone<b.original.candidate.zone;
}
inline Report FromRows(const std::vector<rrzc_direct::Row>& inputs) {
  Report out;std::set<uint64_t> identities;
  for(const auto& row:inputs) {
    const auto& c=row.candidate;const auto& cost=row.cost;
    if(!identities.insert(c.zone).second || !rrzc_simple::Eligible(c,0) ||
        cost.reclaimed!=c.capacity-c.valid || cost.credit>c.valid ||
        cost.net!=c.valid-cost.credit) {
      out.reason="候选重复、资格无效或预计复制账目不一致";return out;
    }
    // 只在同一批已完成实时安全复核的可选集合内比较，不让不安全项
    // 的预测改变归一分数。真实接线必须先复核，再调用本核排名。
    if(!row.safe)continue;
    if(cost.credit>std::numeric_limits<uint64_t>::max()-out.pool_rr) {
      out.reason="候选池RR预计复制总量溢出";return out;
    }
    out.pool_rr+=cost.credit;
  }
  for(const auto& row:inputs) {
    if(!row.safe)continue;
    const uint64_t deferred=out.pool_rr-row.cost.credit;
    if(row.candidate.valid>std::numeric_limits<uint64_t>::max()-deferred) {
      out.rows.clear();out.reason="当前及剩余RR预计复制总量溢出";return out;
    }
    out.rows.push_back({row,deferred,row.candidate.valid+deferred});
  }
  std::sort(out.rows.begin(),out.rows.end(),Less);
  out.known=true;out.reason="同一安全候选池的一次ZC及未选项预计RR总复制/回收空间";
  return out;
}
}  // 总复制实验命名空间结束。
