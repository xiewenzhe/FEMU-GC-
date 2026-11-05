// 合成边界只验证预测内核，不能作为RR节省或性能结果。
#include "rrzc_recent_eta_selector.h"
#include <cassert>
#include <iostream>
int main() {
  constexpr uint64_t m=1ULL<<20,s=rrzc_v1::kSecond;
  rrzc_simple::Candidate c{128*m,5*m,128*m,976,1024,true,true,true};
  rrzc_v1::RecentBlock b;b.zone=c.zone;b.zone_index=1;b.rc=976;b.delta10=b.delta30=976;
  b.flags=7;b.snapshot_ns=100*s;b.epoch_start_ns=s;
  rrzc_v1::RecentReport now;now.session_hi=1;now.session_lo=2;now.threshold=1024;
  now.blocks_per_zone=128;now.zones={b};
  auto before=now;before.zones[0].snapshot_ns-=s;before.zones[0].rc-=16;
  rrzc_v1::GrowthHistory history;history.Push(before);
  rrzc_retention::Forecast f;f.known=true;f.interval10=10*s;f.interval30=30*s;
  auto run=[&]{return rrzc_recent_eta::Estimate(c,&b,&f,now,history,10*s);};
  auto old=rrzc_direct::Estimate(c,&b,&f,1024,10*s);auto recent=run();
  assert(old.eta==491803279 && recent.eta==3*s && recent.credit==5*m);
  f.loss10=m;f.loss30=2*m;
  assert(run().credit<rrzc_direct::Estimate(c,&b,&f,1024,10*s).credit);
  // 相同速率下预测和留存抵扣完全一致，不能偷偷添加时间折扣。
  b.delta10=b.delta30=160;
  old=rrzc_direct::Estimate(c,&b,&f,1024,10*s);recent=run();
  assert(old.eta==recent.eta && old.credit==recent.credit && old.net==recent.net);
  b.delta10=b.delta30=976;
  f.loss10=f.loss30=0;
  // 十秒突发尚在，但同块近期零增长，不给予未来RR抵扣。
  before.zones[0].rc=b.rc;history.Clear();history.Push(before);
  recent=run();assert(recent.risk_known && recent.eta==UINT64_MAX && !recent.credit);
  before.zones[0].rc=b.rc-16;
  for(unsigned mismatch=0;mismatch<7;++mismatch) {
    auto changed=before;
    if(mismatch==0)++changed.session_hi;
    if(mismatch==1)++changed.zones[0].generation;
    if(mismatch==2)++changed.zones[0].block_id;
    if(mismatch==3)++changed.zones[0].plane;
    if(mismatch==4)++changed.zones[0].epoch_start_ns;
    if(mismatch==5)changed.zones[0].snapshot_ns-=2*s;
    if(mismatch==6)changed.zones[0].rc=b.rc+1;
    history.Clear();history.Push(changed);
    assert(!run().risk_known && !run().credit);
  }
  history.Clear();assert(!run().risk_known);
  b.rc=1024;f.known=false;
  recent=run();assert(recent.risk_known && !recent.eta && recent.credit==5*m);
  b.flags=15;assert(!run().risk_known);b.flags=7;b.rc=976;
  history.Push(before);f.known=true;
  std::vector<rrzc_simple::Candidate> cs={c,{256*m,4*m,128*m,0,1024,true,true,true}};
  std::map<uint64_t,rrzc_v1::RecentBlock> bs={{c.zone,b}};
  std::map<uint64_t,rrzc_retention::Forecast> fs={{c.zone,f}};
  auto ranked=rrzc_recent_eta::Rank(cs,bs,fs,now,history,0,10*s);
  assert(ranked.size()==2 && ranked[0].candidate.zone==c.zone);
  // 不给不可预测退役增加特判：稳定历史下8MiB候选仍可能被选择。
  cs[0].valid=8*m;ranked=rrzc_recent_eta::Rank(cs,bs,fs,now,history,0,10*s);
  assert(ranked[0].candidate.zone==c.zone && ranked[0].cost.credit==8*m);
  std::cout<<"近期ETA、零增长、同块/会话/Reset边界及原排序通过；不声称解决突然退役\n";
}
