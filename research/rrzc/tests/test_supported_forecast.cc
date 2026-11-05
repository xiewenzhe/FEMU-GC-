// 独立预测核的受控功能测试与重算接口；不是FEMU设备实验。
#include "rrzc_supported_forecast.h"
#include "rrzc_terminal_v2.h"
#include <cassert>
#include <iostream>
static bool ReadBlock(std::istream& in,rrzc_v1::RecentBlock& b) {
  return bool(in>>b.zone>>b.zone_index>>b.generation>>b.block_id>>b.ch>>b.lun>>b.plane>>b.block
    >>b.snapshot_ns>>b.epoch_start_ns>>b.rc>>b.delta10>>b.delta30>>b.flags);
}
int main(int argc,char**) {
  using namespace rrzc_supported;
  if(argc>1) {
    uint64_t z,v,cap,threshold,hi,lo,blocks,compatible,fk,fz,fg,fv,sl,ss,ll,ls,n;
    while(std::cin>>z>>v>>cap>>threshold>>hi>>lo>>blocks>>compatible) {
      rrzc_v1::RecentBlock b;if(!ReadBlock(std::cin,b))return 2;
      if(!(std::cin>>fk>>fz>>fg>>fv>>sl>>ss>>ll>>ls>>n) || n>4)return 2;
      std::deque<rrzc_v1::RecentReport> history;
      for(uint64_t i=0;i<n;++i) {
        rrzc_v1::RecentReport frame;rrzc_v1::RecentBlock old;uint64_t count;
        if(!(std::cin>>frame.threshold>>frame.session_hi>>frame.session_lo>>frame.blocks_per_zone>>count) || count>1)return 2;
        if(count){if(!ReadBlock(std::cin,old))return 2;frame.zones.push_back(old);}
        history.push_back(frame);
      }
      rrzc_simple::Candidate c{z,v,cap,b.rc,threshold,true,true,true};
      rrzc_window_credit::Retention f;f.known=fk;f.zone=fz;f.generation=fg;f.valid=fv;
      f.short_loss=sl;f.short_span=ss;f.long_loss=ll;f.long_span=ls;
      rrzc_v1::RecentReport report;report.threshold=threshold;report.session_hi=hi;report.session_lo=lo;report.blocks_per_zone=blocks;
      Evidence e;auto cost=Estimate(c,&b,&f,report,history,10*kSecond,&e,
          compatible?Scope::CompatibleRates:Scope::EvidenceOnly);
      std::cout<<cost.risk_known<<' '<<cost.eta<<' '<<cost.retained<<' '<<cost.credit<<' '<<cost.net<<' '<<cost.reclaimed<<' '
        <<e.growth.known<<' '<<e.growth.first_delta<<' '<<e.growth.first_span<<' '
        <<e.growth.second_delta<<' '<<e.growth.second_span<<' '<<e.rate_delta<<' '<<e.rate_span<<' '
        <<e.event_window<<' '<<e.conditional_ns<<' '<<e.probability.known<<' '<<e.probability.units<<' '
        <<e.compatibility.known<<' '<<e.compatibility.units<<'\n';
    }
    return std::cin.eof()?0:2;
  }
  const uint64_t s=kSecond;
  rrzc_simple::Candidate hot{128,5<<20,128<<20,1000,1024,true,true,true};
  rrzc_v1::RecentBlock b;b.zone=128;b.zone_index=1;b.generation=3;b.block_id=2;b.snapshot_ns=100*s;
  b.rc=1000;b.delta10=160;b.delta30=160;b.flags=7;
  rrzc_v1::RecentReport report;report.threshold=1024;report.session_hi=1;report.blocks_per_zone=128;
  std::deque<rrzc_v1::RecentReport> history;
  for(uint64_t age=3;age;--age) {
    auto frame=report;auto p=b;p.snapshot_ns-=age*s;p.rc-=16*age;frame.zones={p};history.push_back(frame);
  }
  rrzc_window_credit::Retention f;f.known=true;f.zone=128;f.generation=3;f.valid=hot.valid;f.short_span=10*s;f.long_span=30*s;
  Evidence e;auto cost=Estimate(hot,&b,&f,report,history,10*s,&e);
  assert(e.growth.known && e.growth.first_delta==16 && e.growth.second_delta==16);
  assert(e.event_window==2*s && e.probability.units>800000000 && cost.credit>4<<20);
  rrzc_simple::Candidate cold{256,4<<20,128<<20,0,1024,true,true,true};
  rrzc_direct::Cost cc;cc.net=cold.valid;cc.reclaimed=cold.capacity-cold.valid;
  std::vector<rrzc_deadline::Input> rows{{{cold,cc,true},0,0},{{hot,cost,true},e.probability.units,
      uint64_t(Wide(hot.capacity-cost.retained)*e.probability.units/rrzc_joint_time::kScale)}};
  rrzc_space_plan::Need need;need.known=true;need.bytes=1;
  auto d=rrzc_deadline::Select(rows,need);assert(d.found && d.zone==hot.zone);
  // 同样累计RC、但上一独立区间停止读取时，不继续使用旧热度。
  history.back().zones[0].rc=b.rc;cost=Estimate(hot,&b,&f,report,history,10*s,&e);
  assert(!cost.credit && e.probability.known && !e.probability.units);
  rows[1].row.cost=cost;rows[1].probability=rows[1].rr_space=0;
  d=rrzc_deadline::Select(rows,need);assert(d.zone==cold.zone);
  // 单次大批退役不会再推出一个有限的“全部失效”截止。
  f.short_loss=f.long_loss=100<<20;f.short_span=f.long_span=4*s;
  uint64_t live=0;assert(Live(32<<20,f,2*s,&live) && live>0 && live<32<<20);
  std::cout<<"持续热读可改选、停读不借旧热度、比例留存与旧触发规则隔离通过\n";
}
