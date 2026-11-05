// 真实设备实验前的确定性边界测试；以下输入是单元测试构造值。
#include "rrzc_space_plan.h"
#include <cassert>
#include <iostream>
using namespace rrzc_space_plan;
static Input Zone(uint64_t id,uint64_t valid,uint64_t credit=0) {
  Input x;x.row.candidate.zone=id;x.row.candidate.valid=valid;x.row.candidate.capacity=128;
  x.row.safe=true;x.row.cost.reclaimed=128-valid;x.row.cost.credit=credit;
  x.rr_space=credit?128-valid:0;return x;
}
int main(int argc,char** argv) {
  if(argc>1) {
    uint64_t free,non_free,waiters,count;
    while(std::cin>>free>>non_free>>waiters>>count) {
      std::vector<Input> rows;
      for(uint64_t i=0;i<count;++i) {
        uint64_t zone,valid,capacity,credit,space,safe;
        assert(bool(std::cin>>zone>>valid>>capacity>>credit>>space>>safe));
        auto x=Zone(zone,valid,credit);x.row.candidate.capacity=capacity;
        x.row.cost.reclaimed=capacity-valid;x.row.safe=safe;x.rr_space=space;rows.push_back(x);
      }
      const auto r=Select(rows,Demand(free,non_free,waiters),std::string(argv[1])=="第二版");
      std::cout<<r.found<<' '<<r.zone<<' '<<r.progress_only<<' '<<r.plans.size()<<'\n';
      for(const auto& p:r.plans) {
        std::cout<<p.first<<' '<<p.sufficient<<' '<<Decimal(p.copy)<<' '<<Decimal(p.reclaimed)<<' '
            <<Decimal(p.future_rr_copy)<<' '<<Decimal(p.future_rr_space)<<' '<<Decimal(p.Total())<<' '<<p.zones.size();
        for(const auto z:p.zones)std::cout<<' '<<z;
        std::cout<<'\n';
      }
    }
    return 0;
  }
  auto need=Demand(17,83,0);assert(need.known && need.bytes==3);
  assert(Demand(20,80,0).bytes==0);assert(Demand(21,79,1).bytes==1);
  assert(Demand(20,81,0).bytes==1);assert(!Demand(0,0,0).known);
  assert(!Demand(UINT64_MAX,1,0).known);
  Need d=Demand(0,535,0);assert(d.bytes==107);
  auto a=Select({Zone(1,4),Zone(2,5,4)},d);assert(a.found && a.zone==2);
  auto b=Select({Zone(1,4),Zone(2,120,119)},d);assert(b.found && b.zone==1);
  assert(b.plans[1].zones.size()==2 && b.plans[1].copy==124);
  auto c=Select({Zone(1,4),Zone(2,5)},Demand(21,79,0));assert(!c.found);
  c=Select({Zone(1,4),Zone(2,5)},Demand(21,79,1));assert(c.found && c.zone==1);
  auto bad=Zone(1,1);bad.row.safe=false;
  assert(Select({bad,Zone(2,5)},d).zone==2);
  auto insufficient=Select({Zone(2,120)},d);
  assert(insufficient.found && insufficient.progress_only && insufficient.zone==2);
  assert(!Select({},d).found);
  auto unknown=Select({Zone(1,4),Zone(2,5)},Need{});
  assert(unknown.progress_only && unknown.zone==1);
  auto zero=Zone(0,4);assert(Select({zero},d).found);
  assert(Decimal(Wide(UINT64_MAX)+1)=="18446744073709551616");
  // 方案中的补充区只用于估算，决策仍只返回一个实际victim。
  assert(b.plans[1].zones.size()==2 && b.zone==1);
  // 同一完整补充集合存在两种首区顺序时，不能因为编号小而先搬更贵的区。
  Need large=Demand(0,1100,0);assert(large.bytes==220);
  assert(Select({Zone(1,30),Zone(2,4)},large).zone==1);
  assert(Select({Zone(1,30),Zone(2,4)},large,true).zone==2);
  std::cout<<"空间需求、无操作、等待进展、补充复制、安全过滤及整数边界通过\n";
}
