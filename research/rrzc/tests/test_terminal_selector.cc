// 合成正反例及随机独立重算桥接；不是设备性能或收益结果。
#include "rrzc_terminal_selector.h"
#include <cassert>
#include <iostream>
using namespace rrzc_terminal;
constexpr uint64_t kSecond=rrzc_v1::kSecond;
static Input Zone(uint64_t z,uint64_t v,uint64_t p=0,uint64_t terminal=UINT64_MAX) {
  Input x;auto& b=x.base;b.row.candidate.zone=z;b.row.candidate.valid=v;b.row.candidate.capacity=128;
  b.row.safe=true;b.row.cost.reclaimed=128-v;b.row.cost.retained=v;
  b.probability=p;b.row.cost.credit=uint64_t(Wide(v)*p/kScale);
  b.rr_space=uint64_t(Wide(128-v)*p/kScale);
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
        b.row.cost.retained=retained;b.row.cost.credit=uint64_t(Wide(retained)*p/kScale);
        b.rr_space=uint64_t(Wide(cap-retained)*p/kScale);b.row.safe=safe;
        x.retention_known=known;x.terminal_valid=terminal;rows.push_back(x);
      }
      const auto r=Select(rows,rrzc_space_plan::Demand(free,nonfree,waiters));
      std::cout<<r.found<<' '<<r.zone<<' '<<r.progress_only<<' '<<r.correction_known<<' '<<r.reference_space<<' '<<r.plans.size()<<'\n';
      for(const auto& p:r.plans) {
        std::cout<<p.base.first<<' '<<p.expected_space<<' '<<p.space_gap<<' '<<rrzc_space_plan::Decimal(p.deferred_copy)
          <<' '<<rrzc_space_plan::Decimal(p.Total())<<' '<<p.priced<<' '<<p.completion.size();
        for(const auto& t:p.completion)std::cout<<' '<<t.zone<<' '<<t.available_space<<' '<<t.full_copy<<' '<<t.priced_space<<' '<<t.priced_copy;
        std::cout<<'\n';
      }
    }
    return 0;
  }
  auto need=rrzc_space_plan::Demand(18,82,0);
  auto cold=Zone(1,4),hot=Zone(2,5,kScale);
  auto r=Select({cold,hot},need);assert(r.correction_known);
  assert(r.plans[0].Total()==9 && r.plans[1].Total()==9);
  assert(r.zone==2 && r.plans[1].deferred_copy==4);
  // 近确定而非确定RR：不再把留下的冷区全部计成免费空间。
  hot=Zone(2,5,kScale*9/10);assert(Select({cold,hot},need).zone==1);
  // 已有留存历史支持冷数据退役时，仍可以真正获益，而非退化为固定Greedy。
  cold=Zone(1,4,0,0);r=Select({cold,hot},need);
  assert(r.zone==2 && r.plans[1].deferred_copy==0);
  assert(Select({Zone(1,4),Zone(2,5)},need).zone==1);
  assert(Select({Zone(1,4),Zone(2,120,kScale*9/10)},need).zone==1);
  auto large=rrzc_space_plan::Demand(0,1100,0);
  r=Select({Zone(1,4),Zone(2,30,kScale)},large);
  assert(r.plans[0].expected_space==222 && r.plans[0].base.reclaimed==222);
  assert(r.plans[1].expected_space==222); // 补充RR回收没有重复加一遍。
  assert(!Select({cold,hot},rrzc_space_plan::Demand(21,79,0)).found);
  assert(Select({hot},large).progress_only);
  hot.base.row.safe=false;assert(Select({Zone(1,4),hot},need).zone==1);
  auto unknown=Zone(2,5,kScale);unknown.base.rr_space++;
  assert(!Select({Zone(1,4),unknown},need).correction_known);
  auto duplicate=Zone(1,4);assert(!Select({duplicate,duplicate},need).correction_known);
  rrzc_simple::Candidate c{1,40,128,0,1024,true,true,true};
  rrzc_window_credit::Retention f;f.known=true;f.zone=1;f.generation=3;f.valid=40;
  f.short_span=10*kSecond;f.long_span=30*kSecond;f.short_loss=10;f.long_loss=15;
  bool known=false;assert(TerminalValid(c,&f,3,10*kSecond,&known)==30 && known);
  assert(TerminalValid(c,&f,4,10*kSecond,&known)==40 && !known);
  std::cout<<"空间边界债务、同区回收不重复、留存正例、停读负例及未知回退通过\n";
}
