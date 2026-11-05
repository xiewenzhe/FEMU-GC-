// 合成边界与真实冻结轨迹桥接；通过不代表真实设备收益。
#include "rrzc_expected_rr_selector.h"
#include <cassert>
#include <iostream>
#include <string>
using namespace rrzc_expected_rr;

int main(int argc,char** argv) {
  if (argc==2 && std::string(argv[1])=="probability") {
    uint64_t margin;long double mean;
    while (std::cin>>margin>>mean) {
      const auto p=ThresholdProbability(margin,mean);
      std::cout<<p.known<<' '<<p.units<<'\n';
    }
    return std::cin.eof() ? 0 : 2;
  }
  if (argc==2 && std::string(argv[1])=="replay") {
    uint64_t z,v,cap,rc,d10,d30,flags,age,threshold,gen,known,fz,fg,fv,sl,ss,ll,ls,gk,gd,gs;
    while (std::cin>>z>>v>>cap>>rc>>d10>>d30>>flags>>age>>threshold>>gen>>known>>fz>>fg>>fv>>sl>>ss>>ll>>ls>>gk>>gd>>gs) {
      rrzc_simple::Candidate c{z,v,cap,rc,threshold,true,true,true};
      rrzc_v1::RecentBlock b;b.zone=z;b.rc=rc;b.delta10=d10;b.delta30=d30;b.flags=uint32_t(flags);
      b.snapshot_ns=age;b.generation=gen;
      Retention f;f.known=known;f.zone=fz;f.generation=fg;f.valid=fv;
      f.short_loss=sl;f.short_span=ss;f.long_loss=ll;f.long_span=ls;
      rrzc_v1::GrowthEvidence g;g.known=gk;g.delta=gd;g.interval_ns=gs;
      Evidence proof;const auto out=EstimateEvidence(c,&b,&f,threshold,10*kSecond,g,&proof);
      std::cout<<out.risk_known<<' '<<out.eta<<' '<<out.retained<<' '<<out.credit<<' '<<out.net
          <<' '<<out.reclaimed<<' '<<proof.persistence.window.window<<' '<<proof.probability.known
          <<' '<<proof.probability.units<<' '<<proof.previous_credit<<'\n';
    }
    return std::cin.eof() ? 0 : 2;
  }
  const uint64_t m=1ULL<<20,s=kSecond;
  rrzc_simple::Candidate hot{128,10*m,128*m,976,1024,true,true,true};
  rrzc_v1::RecentBlock b;b.zone=128;b.rc=b.delta10=b.delta30=976;b.flags=7;
  b.snapshot_ns=100*s;b.generation=3;b.block_id=7;
  Retention f;f.known=true;f.zone=128;f.generation=3;f.valid=hot.valid;
  f.short_span=10*s;f.long_span=30*s;
  rrzc_v1::GrowthEvidence g;g.known=true;g.delta=16;g.interval_ns=s;
  Evidence proof;
  auto run=[&]{return EstimateEvidence(hot,&b,&f,1024,10*s,g,&proof);};
  auto out=run();assert(out.eta==3*s && out.credit>9*m && out.credit<hot.valid && out.net<m);
  rrzc_v1::RecentReport current;current.threshold=1024;current.session_hi=1;current.blocks_per_zone=128;
  rrzc_v1::GrowthHistory history;auto old=b;old.rc-=16;old.snapshot_ns-=s;
  auto previous=current;previous.zones={old};history.Push(previous);
  auto cold=hot;cold.zone=256;cold.valid=4*m;
  auto rows=Rank({cold,hot},{{128,b}},{{128,f}},current,history,0,10*s);
  assert(rows.front().candidate.zone==128);
  // 停止及未知不删候选、不利用旧突发抵扣；没有未来删除信号就不能预知突然退役。
  g.delta=0;assert(!run().credit);g.delta=16;g.known=false;assert(!run().credit);
  g.known=true;f.known=false;assert(!run().credit);f.known=true;
  f.short_loss=80*m;f.long_loss=100*m;assert(!run().credit);
  f.short_loss=f.long_loss=0;
  auto changed=b;changed.block_id++;
  assert(!Estimate(hot,&changed,&f,current,history,10*s).credit);
  changed=b;changed.generation++;assert(!Estimate(hot,&changed,&f,current,history,10*s).credit);
  rows=Rank({hot},{{128,b}},{{128,f}},current,{},0,10*s);
  assert(rows.size()==1 && !rows.front().cost.credit);
  b.rc=b.delta10=b.delta30=1024;g.known=false;
  assert(run().credit==hot.valid && proof.probability.units==kProbabilityScale);
  b.flags=9;assert(!run().credit);
  assert(!ThresholdProbability(kMaxTerms+1,1).known);
  assert(ThresholdProbability(1,0).known && !ThresholdProbability(1,0).units);
  assert(!ThresholdProbability(1,-1).known);
  std::cout<<"期望成本核、持续/停止、未知、退役速率及身份边界通过；不声称真实RR收益\n";
}
