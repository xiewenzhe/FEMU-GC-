// 验证单位回收评分的一般性质；下列数据是合成测试，不是收益实验。
#include "rrzc_unit_cost_selector.h"
#include <cassert>
#include <algorithm>

rrzc_deadline::Input Make(uint64_t zone,uint64_t valid,uint64_t capacity,uint64_t credit,
                         bool known=true,bool safe=true) {
  rrzc_deadline::Input x{};x.row.candidate.zone=zone;x.row.candidate.valid=valid;
  x.row.candidate.capacity=capacity;x.row.safe=safe;x.row.cost.risk_known=known;
  x.row.cost.credit=credit;x.row.cost.reclaimed=capacity-valid;return x;
}
int main() {
  using rrzc_unit_cost::Select;
  // 风险全部为零、容量相等时退化为最少有效量；平分由Zone身份稳定打破。
  auto a=Select({Make(8,30,128,0),Make(4,10,128,0),Make(2,10,128,0)});
  assert(a.found && a.zone==2);
  // 空间收益相近且有真实估计的候选可以直接胜出，不预设Greedy。
  assert(Select({Make(1,20,128,0),Make(2,40,128,35)}).zone==2);
  // 高风险不足以补偿高复制及低回收收益时仍选择空间更划算者。
  assert(Select({Make(1,5,128,0),Make(2,75,128,45)}).zone==1);
  // 未知风险不借用非零字段；无效输入及不安全候选不参选。
  assert(Select({Make(1,10,128,0),Make(2,70,128,70,false)}).zone==1);
  assert(!Select({Make(1,10,128,11),Make(2,10,128,0,true,false),Make(3,128,128,0)}).found);
  auto wrong=Make(1,10,128,0);++wrong.row.cost.reclaimed;assert(!Select({wrong}).found);
  // 整数交叉相乘必须使用128位，不能因64位溢出改变选择。
  const uint64_t big=uint64_t(1)<<62;
  std::vector<rrzc_deadline::Input> rows={Make(2,big,big*2,big/2),Make(1,big/2,big*2,0)};
  const auto expected=Select(rows).zone;assert(expected==1);
  std::reverse(rows.begin(),rows.end());assert(Select(rows).zone==expected);
  return 0;
}
