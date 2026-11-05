// 合成属性测试，不是实验结果；检查空间需求变化时的连续定价及整数边界。
#include "rrzc_reclaim_price_selector.h"
#include <cassert>

rrzc_deadline::Input Make(uint64_t z,uint64_t valid,uint64_t retained,uint64_t capacity=128) {
  rrzc_deadline::Input x{};x.row.candidate.zone=z;x.row.candidate.valid=valid;
  x.row.candidate.capacity=capacity;x.row.safe=true;x.row.cost.risk_known=true;
  x.row.cost.retained=retained;x.row.cost.credit=retained;x.row.cost.reclaimed=capacity-valid;
  x.probability=rrzc_deadline::kScale;x.rr_space=capacity-retained;return x;
}
int main() {
  using rrzc_reclaim_price::Select;
  auto a=Make(1,20,0),b=Make(2,40,35);a.rr_space=0;a.probability=0;
  rrzc_space_plan::Need need{};need.known=true;need.bytes=10;
  std::vector<rrzc_reclaim_price::Evidence> e;
  assert(Select({a,b},need,&e).zone==2); // 缺口很小时，模型仍可直接选联合候选。
  assert(e.size()==2);
  for(const auto& r:e)assert(r.raw_credit==r.debit+r.effective_credit && r.debit<=r.raw_credit);
  need.bytes=128;
  assert(Select({a,b},need,&e).zone==1); // 缺口大时不能漏掉RR回收的替代代价。
  need.bytes=0;need.waiters=1;assert(Select({a,b},need,&e).zone==1);
  for(const auto& r:e)assert(r.full_space);
  need.waiters=0;need.known=false;assert(Select({a,b},need).zone==1);
  // 充分空间压力下，不得只靠取消一次同粒度RR制造低于空间最低复制价格的收益。
  need.known=true;need.bytes=UINT64_MAX;
  for(uint64_t v=21;v<120;++v)for(uint64_t retained=0;retained<=v;retained+=7) {
    const auto d=Select({a,Make(2,v,retained)},need);
    assert(d.zone==1);
  }
  auto unsafe=b;unsafe.row.safe=false;need.bytes=0;
  assert(Select({a,unsafe},need).zone==1);
  assert(!rrzc_reclaim_price::Select({},need).found);
  // 大数乘积使用128位，中间估价在转回64位前封顶。
  const uint64_t big=uint64_t(1)<<62;
  a=Make(1,big/4,0,big*2);a.rr_space=0;
  b=Make(2,big,big,big*2);need.bytes=big*2;
  assert(Select({a,b},need,&e).zone==1);
  return 0;
}
