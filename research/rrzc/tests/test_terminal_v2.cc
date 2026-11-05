// 取整一致性正反例，均为合成输入；不冒充真实设备收益。
#include "rrzc_terminal_v2.h"
#include <cassert>
#include <iostream>
using rrzc_terminal::Input;
constexpr uint64_t MiB=1ULL<<20,S=1000000000;
static Input Zone(uint64_t id,uint64_t v,uint64_t p=0,uint64_t terminal=UINT64_MAX) {
  Input x;auto& b=x.base;b.row.candidate.zone=id;b.row.candidate.valid=v;b.row.candidate.capacity=128*MiB;
  b.row.cost.retained=v;b.row.cost.reclaimed=128*MiB-v;b.row.safe=true;b.probability=p;
  b.row.cost.credit=uint64_t(__uint128_t(v)*p/S);b.rr_space=uint64_t(__uint128_t(128*MiB-v)*p/S);
  x.retention_known=terminal!=UINT64_MAX;x.terminal_valid=x.retention_known?terminal:v;return x;
}
int main(int argc,char**) {
  if(argc>1) {
    uint64_t free,nonfree,waiters,count;
    while(std::cin>>free>>nonfree>>waiters>>count) {
      std::vector<Input> rows;
      for(uint64_t i=0;i<count;++i) {
        uint64_t z,v,cap,retained,p,safe,known,terminal;
        assert(bool(std::cin>>z>>v>>cap>>retained>>p>>safe>>known>>terminal));
        auto x=Zone(z,v,p);auto& b=x.base;b.row.candidate.capacity=cap;b.row.cost.reclaimed=cap-v;
        b.row.cost.retained=retained;b.row.cost.credit=uint64_t(__uint128_t(retained)*p/S);
        b.rr_space=uint64_t(__uint128_t(cap-retained)*p/S);b.row.safe=safe;
        x.retention_known=known;x.terminal_valid=terminal;rows.push_back(x);
      }
      const auto out=rrzc_terminal_v2::Select(rows,rrzc_space_plan::Demand(free,nonfree,waiters));
      std::cout<<out.found<<' '<<out.zone<<' '<<out.correction_known<<' '<<out.plans.size()<<'\n';
      for(const auto& p:out.plans)std::cout<<p.base.base.first<<' '<<p.rr_rounding<<' '<<rrzc_space_plan::Decimal(p.Total())<<'\n';
    }
    return 0;
  }
  const auto need=rrzc_space_plan::Demand(18,82,0);
  auto cold=Zone(1,4*MiB),hot=Zone(2,8*MiB,S-1);
  const auto first=rrzc_terminal::Select({cold,hot},need);
  assert(first.zone==1 && first.plans[1].Total()-first.plans[0].Total()==1);
  auto fair=rrzc_terminal_v2::Select({cold,hot},need);
  assert(fair.zone==2 && fair.plans[0].Total()==12*MiB && fair.plans[1].Total()==12*MiB);
  // 把9MiB对5MiB的短窗假象校正成两种方案均约9MiB，而不是为减少RR伪造字节收益。
  hot=Zone(2,5*MiB,S-1);fair=rrzc_terminal_v2::Select({cold,hot},need);
  assert(fair.zone==2 && fair.plans[0].Total()==9*MiB && fair.plans[1].Total()==9*MiB);
  // 有可观察留存下降时仍可优先联合维护；未知留存不变成免费空间。
  cold=Zone(1,4*MiB,0,0);fair=rrzc_terminal_v2::Select({cold,hot},need);
  assert(fair.zone==2 && fair.plans[1].Total()==5*MiB);
  cold=Zone(1,4*MiB);hot=Zone(2,5*MiB,S*9/10);
  fair=rrzc_terminal_v2::Select({cold,hot},need);assert(fair.zone==1);
  assert(rrzc_terminal_v2::Select({cold,Zone(2,5*MiB)},need).zone==1);
  std::cout<<"首版一字节改选已复现；统一向上取整后不再计为收益，留存正例与停读负例通过\n";
}
