// 合成边界与日志重放桥接；离线通过不能冒充实际维护节省。
#include "rrzc_deadline_selector.h"
#include <cassert>
#include <iostream>
using namespace rrzc_deadline;
static Input Zone(uint64_t z,uint64_t v,uint64_t p=0) {
  Input x;x.row.candidate.zone=z;x.row.candidate.valid=v;x.row.candidate.capacity=128;
  x.row.safe=true;x.row.cost.reclaimed=128-v;x.row.cost.retained=v;
  x.probability=p;x.row.cost.credit=uint64_t(Wide(v)*p/kScale);return x;
}
int main(int argc,char** argv) {
  if(argc>1 && std::string(argv[1])=="预测") {
    uint64_t z,v,cap,rc,d10,d30,flags,age,threshold,gen,fk,fz,fg,fv,sl,ss,ll,ls,gk,gd,gs;
    while(std::cin>>z>>v>>cap>>rc>>d10>>d30>>flags>>age>>threshold>>gen>>fk>>fz>>fg>>fv>>sl>>ss>>ll>>ls>>gk>>gd>>gs) {
      rrzc_simple::Candidate c{z,v,cap,rc,threshold,true,true,true};
      rrzc_v1::RecentBlock b;b.zone=z;b.rc=rc;b.delta10=d10;b.delta30=d30;b.flags=flags;
      b.snapshot_ns=age;b.generation=gen;
      rrzc_window_credit::Retention f;f.known=fk;f.zone=fz;f.generation=fg;f.valid=fv;
      f.short_loss=sl;f.short_span=ss;f.long_loss=ll;f.long_span=ls;
      rrzc_v1::GrowthEvidence g;g.known=gk;g.delta=gd;g.interval_ns=gs;
      Evidence e;const auto cost=EstimateEvidence(c,&b,&f,threshold,10*kSecond,g,&e);
      std::cout<<cost.risk_known<<' '<<cost.eta<<' '<<cost.retained<<' '<<cost.credit<<' '<<cost.net<<' '
        <<cost.reclaimed<<' '<<e.window.window<<' '<<e.probability.known<<' '<<e.probability.units<<'\n';
    }
    return std::cin.eof()?0:2;
  }
  if(argc>1) {
    uint64_t free,nonfree,waiters,count;
    while(std::cin>>free>>nonfree>>waiters>>count) {
      std::vector<Input> inputs;
      for(uint64_t i=0;i<count;++i) {
        uint64_t z,v,cap,credit,p,space,safe;
        assert(bool(std::cin>>z>>v>>cap>>credit>>p>>space>>safe));
        auto x=Zone(z,v,p);x.row.candidate.capacity=cap;x.row.cost.reclaimed=cap-v;
        x.row.cost.credit=credit;x.rr_space=space;x.row.safe=safe;inputs.push_back(x);
      }
      const auto out=Select(inputs,rrzc_space_plan::Demand(free,nonfree,waiters));
      std::cout<<out.found<<' '<<out.zone<<' '<<out.progress_only<<' '<<out.plans.size()<<'\n';
      for(const auto& p:out.plans) {
        std::cout<<p.first<<' '<<p.sufficient<<' '<<rrzc_space_plan::Decimal(p.copy)<<' '
          <<rrzc_space_plan::Decimal(p.reclaimed)<<' '<<rrzc_space_plan::Decimal(p.rr_copy)<<' '
          <<rrzc_space_plan::Decimal(p.rr_space)<<' '<<rrzc_space_plan::Decimal(p.Total())<<' '<<p.zones.size();
        for(auto z:p.zones)std::cout<<' '<<z;
        std::cout<<'\n';
      }
    }
    return 0;
  }
  auto need=rrzc_space_plan::Demand(0,1100,0);
  auto cold=Zone(1,4),hot=Zone(2,30,kScale);
  auto result=Select({cold,hot},need);
  assert(result.zone==2 && result.plans[0].Total()==34 && result.plans[1].Total()==34);
  assert(result.plans[0].rr_copy==30 && result.plans[1].rr_copy==0);
  assert(Select({Zone(1,4),Zone(2,30)},need).zone==1);
  assert(!Select({cold,hot},rrzc_space_plan::Demand(21,79,0)).found);
  assert(Select({hot},need).progress_only);
  cold.row.safe=false;assert(Select({cold,hot},need).zone==2);
  const auto s=kSecond;
  rrzc_simple::Candidate c{128,5ULL<<20,128ULL<<20,965,1024,true,true,true};
  rrzc_v1::RecentBlock b;b.zone=128;b.rc=965;b.delta10=903;b.delta30=965;
  b.flags=7;b.snapshot_ns=100*s;b.block_id=113;
  rrzc_window_credit::Retention f;f.known=true;f.zone=128;f.valid=c.valid;f.short_span=10*s;f.long_span=30*s;
  rrzc_v1::RecentReport r;r.session_hi=1;r.threshold=1024;r.blocks_per_zone=128;
  History history;rrzc_v1::GrowthHistory old_history;
  auto push=[&](uint64_t ago,uint64_t rc,uint32_t id=113,uint64_t gen=0) {
    auto p=b;p.snapshot_ns-=ago;p.rc=rc;p.block_id=id;p.generation=gen;
    auto old=r;old.zones={p};history.Push(old);old_history.Push(old);
  };
  push(3500000000ULL,765);push(2500000000ULL,865);push(1500000000ULL,965);push(500000000ULL,965);
  Evidence e;
  const auto cost=Estimate(c,&b,&f,r,history,10*s,&e);
  assert(e.growth.known && e.growth.interval_ns==2500000000ULL && e.growth.delta==100 && cost.credit>0);
  assert(!rrzc_expected_rr::Estimate(c,&b,&f,r,old_history,10*s).credit);
  auto changed=b;changed.block_id++;assert(!Estimate(c,&changed,&f,r,history,10*s).credit);
  changed=b;changed.generation++;assert(!Estimate(c,&changed,&f,r,history,10*s).credit);
  history.Clear();push(3500000000ULL,965);push(2500000000ULL,965);push(1500000000ULL,965);push(500000000ULL,965);
  assert(!Estimate(c,&b,&f,r,history,10*s).credit);
  history.Clear();push(3500000000ULL,765);push(2500000000ULL,865);push(1500000000ULL,900,114);push(500000000ULL,965);
  assert(!Estimate(c,&b,&f,r,history,10*s).credit);
  std::cout<<"时序替代不重复复制、同成本强制RR排序、短停顿、多帧身份与Reset边界通过\n";
}
