// 合成测试只验证算法与边界；不是FEMU测量或RR节省证据。
#include "rrzc_stable_credit_selector.h"
#include <cassert>
#include <iostream>
#include <string>
int main(int argc,char** argv) {
  if (argc==2 && std::string(argv[1])=="replay") {
    uint64_t zone,valid,capacity,rc,d10,d30,flags,age,threshold,known,delta,span;
    uint64_t retained_known,loss10,loss30,interval10,interval30;
    while (std::cin>>zone>>valid>>capacity>>rc>>d10>>d30>>flags>>age>>threshold>>known>>delta>>span
        >>retained_known>>loss10>>loss30>>interval10>>interval30) {
      rrzc_simple::Candidate c{zone,valid,capacity,rc,threshold,true,true,true};
      rrzc_v1::RecentBlock b;b.zone=zone;b.rc=rc;b.delta10=d10;b.delta30=d30;
      b.flags=uint32_t(flags);b.snapshot_ns=age;
      rrzc_v1::GrowthEvidence g;g.known=known;g.delta=delta;g.interval_ns=span;
      rrzc_retention::Forecast f;f.known=retained_known;f.loss10=loss10;f.loss30=loss30;
      f.interval10=interval10;f.interval30=interval30;
      rrzc_stable_credit::Evidence proof;
      const auto cost=rrzc_stable_credit::EstimateEvidence(c,&b,&f,threshold,10*rrzc_v1::kSecond,g,&proof);
      std::cout<<cost.risk_known<<' '<<cost.eta<<' '<<cost.retained<<' '<<cost.credit<<' '<<cost.net
               <<' '<<cost.reclaimed<<' '<<proof.short_eta<<' '<<proof.average_eta<<'\n';
    }
    return std::cin.eof() ? 0 : 2;
  }
  constexpr uint64_t m=1ULL<<20,s=rrzc_v1::kSecond;
  rrzc_simple::Candidate hot{128*m,5*m,128*m,976,1024,true,true,true};
  rrzc_simple::Candidate cold{256*m,4*m,128*m,0,1024,true,true,true};
  rrzc_v1::RecentBlock b;b.zone=hot.zone;b.zone_index=1;b.flags=7;
  b.snapshot_ns=100*s;b.epoch_start_ns=s;b.rc=976;b.delta10=160;b.delta30=480;
  rrzc_v1::RecentReport now;now.threshold=1024;now.session_hi=1;now.session_lo=2;
  now.blocks_per_zone=128;now.zones={b};
  auto previous=now;previous.zones[0].snapshot_ns-=s;previous.zones[0].rc-=16;
  rrzc_v1::GrowthHistory history;history.Push(previous);
  rrzc_retention::Forecast f;f.known=true;f.interval10=10*s;f.interval30=30*s;
  auto run=[&]{return rrzc_stable_credit::Estimate(hot,&b,&f,now,history,10*s);};
  auto cost=run();assert(cost.risk_known && cost.eta==3*s);
  assert(cost.credit==5*m*10/13 && cost.credit<cost.retained && cost.net==hot.valid-cost.credit);
  auto rank=[&] {
    return rrzc_stable_credit::Rank({cold,hot},{{hot.zone,b}},{{hot.zone,f}},now,history,0,10*s);
  };
  // 明确机会可以改变空间排序；近阈值并不自动压过很划算的回收。
  assert(rank()[0].candidate.zone==hot.zone);
  hot.valid=24*m;assert(rank()[0].candidate.zone==cold.zone);hot.valid=5*m;
  // 十秒外有折减而非全额抵扣，并保持边界连续方向。
  b.delta10=24;b.delta30=72;cost=run();assert(cost.eta==20*s && cost.credit==5*m/3);
  assert(rrzc_recent_eta::Estimate(hot,&b,&f,now,history,10*s).eta==3*s);
  b.delta10=48;b.delta30=144;const auto at=run();
  b.delta10=47;b.delta30=141;const auto after=run();
  assert(at.eta==10*s && at.credit==5*m/2 && after.eta>10*s && after.credit>0 && after.credit<at.credit);
  // 不能用持续平均速率掩盖近期停止，也不能用近期加速覆盖过去的慢速。
  b.delta10=160;b.delta30=480;
  previous.zones[0].rc=b.rc;history.Clear();history.Push(previous);
  assert(run().risk_known && run().eta==UINT64_MAX && !run().credit);
  previous.zones[0].rc=b.rc-16;history.Clear();history.Push(previous);
  f.loss10=20*m;f.loss30=60*m;assert(!run().credit);f.loss10=f.loss30=0;
  f.known=false;assert(run().risk_known && !run().credit);f.known=true;
  for (unsigned change=0;change<9;++change) {
    auto p=previous;
    if (change==0) ++p.session_lo;
    if (change==1) ++p.zones[0].generation;
    if (change==2) ++p.zones[0].block_id;
    if (change==3) ++p.zones[0].plane;
    if (change==4) ++p.zones[0].epoch_start_ns;
    if (change==5) p.zones[0].rc=b.rc+1;
    if (change==6) p.zones[0].snapshot_ns-=2*s;
    if (change==7) ++p.threshold;
    if (change==8) ++p.blocks_per_zone;
    history.Clear();history.Push(p);assert(!run().risk_known && !run().credit);
  }
  history.Clear();history.Push(previous);
  b.flags=1;assert(!run().risk_known && !run().credit);
  b.flags=15;assert(!run().risk_known && !run().credit);
  b.flags=7;b.rc=1024;f.known=false;
  assert(run().risk_known && run().eta==0 && run().credit==hot.valid);
  b.rc=976;f.known=true;hot.covered=false;assert(rank().size()==1);hot.covered=true;
  assert(rrzc_stable_credit::Rank({hot},{},{},now,history,128*m,10*s).empty());
  // 无法预见突然失效：稳定退役历史为零时，模型仍可能选择随后退役的热数据。
  hot.valid=8*m;assert(rank()[0].candidate.zone==hot.zone);
  std::cout<<"稳定速率、折减排序、Reset/换块边界通过；不声称解决突然退役或真实RR收益\n";
}
