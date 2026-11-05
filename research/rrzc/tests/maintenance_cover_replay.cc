// 只重算历史候选评分，不访问设备，也不模拟改选后的后续路径。
#include "rrzc_maintenance_cover.h"
#include <iostream>
int main() {
  unsigned n,level;uint64_t free,non_free,waiters;
  while(std::cin>>n>>free>>non_free>>waiters>>level) {
    std::vector<rrzc_deadline::Input> inputs;
    for(unsigned i=0;i<n;++i) {
      rrzc_deadline::Input x{};int safe,known;
      if(!(std::cin>>x.row.candidate.zone>>x.row.candidate.valid>>x.row.candidate.capacity
          >>x.row.cost.credit>>safe>>known))return 2;
      x.row.safe=safe;x.row.cost.risk_known=known;
      x.row.cost.reclaimed=x.row.candidate.capacity-x.row.candidate.valid;inputs.push_back(x);
    }
    const auto need=rrzc_maintenance_cover::ExitNeed(free,non_free,waiters,level);
    const auto out=rrzc_maintenance_cover::Select(inputs,need);
    std::cout<<"D "<<out.found<<' '<<out.zone<<' '<<out.invalid<<' '<<out.space_unknown
        <<' '<<need.known<<' '<<need.target<<' '<<need.bytes<<' '<<out.plans.size()<<'\n';
    for(const auto& p:out.plans) {
      std::cout<<"P "<<p.first<<' '<<p.covered<<' '<<rrzc_space_plan::Decimal(p.copy)
          <<' '<<rrzc_space_plan::Decimal(p.pending_rr)<<' '<<rrzc_space_plan::Decimal(p.reclaimed)
          <<' '<<rrzc_space_plan::Decimal(p.deferred_valid)<<' '<<p.zones.size();
      for(auto z:p.zones)std::cout<<' '<<z;
      std::cout<<'\n';
    }
  }
  return std::cin.eof()?0:2;
}
