// 时间口径边界和独立重算桥接；合成数据不冒充设备实验。
#include "rrzc_joint_time_selector.h"
#include <cassert>
#include <iostream>
int main(int argc,char**) {
  using namespace rrzc_joint_time;
  if(argc>1) {
    uint64_t z,v,cap,rc,d10,d30,flags,age,threshold,gen,fk,fz,fg,fv,sl,ss,ll,ls,gk,gd,gs;
    while(std::cin>>z>>v>>cap>>rc>>d10>>d30>>flags>>age>>threshold>>gen>>fk>>fz>>fg>>fv>>sl>>ss>>ll>>ls>>gk>>gd>>gs) {
      rrzc_simple::Candidate c{z,v,cap,rc,threshold,true,true,true};
      rrzc_v1::RecentBlock b;b.zone=z;b.rc=rc;b.delta10=d10;b.delta30=d30;b.flags=flags;b.snapshot_ns=age;b.generation=gen;
      rrzc_window_credit::Retention f;f.known=fk;f.zone=fz;f.generation=fg;f.valid=fv;
      f.short_loss=sl;f.short_span=ss;f.long_loss=ll;f.long_span=ls;
      rrzc_v1::GrowthEvidence g;g.known=gk;g.delta=gd;g.interval_ns=gs;
      Evidence e;auto cst=EstimateEvidence(c,&b,&f,threshold,10*kSecond,g,&e);
      std::cout<<cst.risk_known<<' '<<cst.eta<<' '<<cst.retained<<' '<<cst.credit<<' '<<cst.net<<' '
          <<cst.reclaimed<<' '<<e.base.window.window<<' '<<e.base.probability.known<<' '<<e.base.probability.units<<' '
          <<e.lifetime_ns<<' '<<e.event_window_ns<<' '<<e.conditional_ns<<'\n';
    }
    return std::cin.eof()?0:2;
  }
  uint64_t ns=0;
  assert(ConditionalTime(1,1,1000000000,&ns) && ns>418000000 && ns<419000000);
  assert(ConditionalTime(1024,.001L,1000000000,&ns) && ns<=1000000000 && ns>990000000);
  assert(!ConditionalTime(0,1,100,&ns) && !ConditionalTime(1,0,100,&ns));
  const auto s=kSecond;
  rrzc_simple::Candidate c{128,19702411,134217728,1002,1024,true,true,true};
  rrzc_v1::RecentBlock b;b.zone=128;b.rc=1002;b.delta10=21;b.delta30=1002;b.flags=7;b.snapshot_ns=100*s;b.generation=3;
  rrzc_window_credit::Retention f;f.known=true;f.zone=128;f.generation=3;f.valid=c.valid;
  f.short_span=10357537698;f.long_span=17357645971;f.long_loss=114328204;
  rrzc_v1::GrowthEvidence g;g.known=true;g.delta=3;g.interval_ns=2357308832;
  Evidence e;auto cost=EstimateEvidence(c,&b,&f,1024,10*s,g,&e);
  assert(e.event_window_ns<3*s && e.base.probability.known && !e.base.probability.units && !cost.credit);
  // 去掉退役速率后仍允许均值ETA超出窗口的非零到达概率，不引入硬ETA门槛。
  f.long_loss=0;cost=EstimateEvidence(c,&b,&f,1024,10*s,g,&e);
  assert(cost.eta>10*s && e.base.probability.units && cost.credit && e.conditional_ns<10*s);
  std::cout<<"同窗事件、条件时间、退役截止、均值越窗和旧速率准入边界通过\n";
}
