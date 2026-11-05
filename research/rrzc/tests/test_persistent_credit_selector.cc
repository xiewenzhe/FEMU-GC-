// 合成边界和真实日志重放桥接；不是新策略实际RR收益。
#include "rrzc_persistent_credit_selector.h"
#include <cassert>
#include <iostream>
#include <string>
using namespace rrzc_persistent_credit;

int main(int argc,char** argv) {
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
          <<' '<<out.reclaimed<<' '<<proof.window.read_span<<' '<<proof.window.window
          <<' '<<proof.average_eta<<' '<<proof.recent_eta<<' '<<proof.original_credit<<'\n';
    }
    return std::cin.eof() ? 0 : 2;
  }
  const uint64_t m=1ULL<<20,s=kSecond;
  rrzc_simple::Candidate c{128,5*m,128*m,975,1024,true,true,true};
  rrzc_v1::RecentBlock b;b.zone=c.zone;b.rc=975;b.delta10=975;b.delta30=975;
  b.snapshot_ns=100*s;b.flags=7;b.generation=3;b.block_id=7;
  Retention f;f.known=true;f.zone=c.zone;f.generation=3;f.valid=c.valid;
  f.short_span=10*s;f.long_span=30*s;
  rrzc_v1::GrowthEvidence g;g.known=true;g.delta=16;g.interval_ns=s;
  Evidence proof;
  auto run=[&]{return EstimateEvidence(c,&b,&f,1024,10*s,g,&proof);};
  auto cost=run();assert(cost.eta==3062500000ULL && cost.credit && cost.credit<proof.original_credit);
  // 持续读机会仍可胜过4MiB冷区；不是先选Greedy或只看最高RC。
  rrzc_v1::RecentReport current;current.threshold=1024;current.session_hi=1;current.blocks_per_zone=128;
  rrzc_v1::GrowthHistory history;auto old=b;old.rc-=16;old.snapshot_ns-=s;
  auto previous=current;previous.zones={old};history.Push(previous);
  auto cold=c;cold.zone=256;cold.valid=4*m;
  auto rows=Rank({cold,c},{{c.zone,b}},{{c.zone,f}},current,history,0,10*s);
  assert(rows.front().candidate.zone==c.zone);
  // 零增长、未知和Reset/换块不拼接；该源仍以空间成本参与排序，不删除候选。
  g.delta=0;assert(run().eta==UINT64_MAX && !run().credit);
  rows=Rank({c},{{c.zone,b}},{{c.zone,f}},current,{},0,10*s);
  assert(rows.size()==1 && !rows.front().cost.credit);
  auto changed=b;changed.block_id++;
  assert(!Estimate(c,&changed,&f,current,history,10*s,&proof).credit && !proof.growth.known);
  changed=b;changed.generation++;
  assert(!Estimate(c,&changed,&f,current,history,10*s,&proof).credit && !proof.growth.known);
  auto other=current;other.session_hi++;
  assert(!Estimate(c,&b,&f,other,history,10*s,&proof).credit && !proof.growth.known);
  // 新近持续速率更快时不放大旧信用；有效量快速退役仍可令收益归零。
  g.delta=200;cost=run();assert(cost.eta==proof.average_eta && cost.credit==proof.original_credit);
  g.delta=16;f.short_loss=80*m;f.long_loss=100*m;assert(!run().credit);
  f.short_loss=f.long_loss=0;g.known=false;assert(!run().credit);g.known=true;
  // 已达阈值不要求增长证据，保持原强制维护语义。
  b.rc=b.delta10=b.delta30=1024;g.known=false;
  assert(run().eta==0 && run().credit==c.valid);
  b.flags=9;assert(!run().credit);
  c.covered=false;assert(Rank({c},{{c.zone,b}},{{c.zone,f}},current,history,0,10*s).empty());
  std::cout<<"持续读修正、停止/变慢、Reset/换块和直接排序通过；不声称真实RR收益\n";
}
