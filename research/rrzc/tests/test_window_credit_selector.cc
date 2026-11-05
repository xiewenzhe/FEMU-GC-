// 合成边界与重放桥接；不是实际RR或性能收益证据。
#include "rrzc_window_credit_selector.h"
#include <cassert>
#include <iostream>
#include <string>
using namespace rrzc_window_credit;

static rrzc_retention::Frame Frame(uint64_t t,uint64_t valid,bool known=true) {
  rrzc_retention::Frame f;f.session_hi=1;f.session_lo=2;
  rrzc_retention::Point p;p.zone=128;p.time=t;p.valid=valid;p.known=known;
  p.device_generation=3;p.host_generation=3;p.host_sequence=6;f.zones[p.zone]=p;
  f.before.enabled=f.after.enabled=f.before.reliable=f.after.reliable=true;
  f.before.owner=f.after.owner=1;f.before.created_ns=f.after.created_ns=1;
  f.before.captured_ns=t-1;f.after.captured_ns=t+1;return f;
}

int main(int argc,char** argv) {
  if (argc==2 && std::string(argv[1])=="replay") {
    uint64_t z,v,cap,rc,d10,d30,flags,age,threshold,gen,known,fz,fg,fv,sl,ss,ll,ls;
    while(std::cin>>z>>v>>cap>>rc>>d10>>d30>>flags>>age>>threshold>>gen>>known>>fz>>fg>>fv>>sl>>ss>>ll>>ls) {
      rrzc_simple::Candidate c{z,v,cap,rc,threshold,true,true,true};
      rrzc_v1::RecentBlock b;b.zone=z;b.rc=rc;b.delta10=d10;b.delta30=d30;b.flags=uint32_t(flags);
      b.snapshot_ns=age;b.generation=gen;
      Retention f;f.known=known;f.zone=fz;f.generation=fg;f.valid=fv;
      f.short_loss=sl;f.short_span=ss;f.long_loss=ll;f.long_span=ls;
      Evidence proof;auto cost=Estimate(c,&b,&f,threshold,10*kSecond,&proof);
      std::cout<<cost.risk_known<<' '<<cost.eta<<' '<<cost.retained<<' '<<cost.credit<<' '<<cost.net
               <<' '<<cost.reclaimed<<' '<<proof.read_span<<' '<<proof.window<<'\n';
    }
    return std::cin.eof() ? 0 : 2;
  }
  const uint64_t m=1ULL<<20,s=kSecond;
  rrzc_simple::Candidate c{128,8*m,128*m,1000,1024,true,true,true};
  rrzc_v1::RecentBlock b;b.zone=c.zone;b.generation=3;b.snapshot_ns=100*s;b.flags=7;
  b.rc=1000;b.delta10=240;b.delta30=480;
  Retention f;f.known=true;f.zone=c.zone;f.generation=3;f.valid=c.valid;
  f.short_span=2*s;f.long_span=2*s;
  Evidence proof;
  auto run=[&]{return Estimate(c,&b,&f,1024,10*s,&proof);};
  auto cost=run();assert(cost.eta==s && cost.credit==c.valid/3 && proof.window==2*s);
  // 不要求三十秒留存；可见覆盖越长，支撑同一预测的收益不下降。
  f.short_span=f.long_span=10*s;cost=run();assert(cost.credit==c.valid*9/11);
  b.delta10=24;cost=run();assert(cost.eta==10*s && !cost.credit);
  b.delta10=12;cost=run();assert(cost.eta==20*s && !cost.credit);
  b.delta10=0;b.delta30=720;cost=run();assert(cost.eta==UINT64_MAX && !cost.credit);
  b.delta30=0;assert(run().eta==UINT64_MAX && !run().credit);
  b.delta10=240;b.delta30=480;f.known=false;assert(!run().credit);f.known=true;
  f.short_loss=80*m;f.long_loss=100*m;assert(!run().credit);f.short_loss=f.long_loss=0;
  f.generation=4;assert(!run().credit);f.generation=3;
  f.valid=c.valid+1;assert(!run().credit);f.valid=c.valid;
  f.zone=999;assert(!run().credit);f.zone=c.zone;
  // Reset后前缀只在完整同块计数可验证时使用，不拼接Reset前读历史。
  b.snapshot_ns=5*s;b.flags=1;b.rc=b.delta10=b.delta30=900;
  cost=run();assert(cost.risk_known && cost.eta==Eta(124,5*s,900) && cost.credit);
  b.delta10=800;assert(!run().risk_known && !run().credit);
  b.rc=b.delta10=b.delta30=1024;assert(run().eta==0 && run().credit==c.valid);
  b.flags=9;assert(!run().credit);
  History history;
  history.Push(Frame(98*s,10*m));history.Push(Frame(99*s,9*m));auto current=Frame(100*s,8*m);
  auto live=history.Check(current,current.zones.at(128));
  assert(live.known && live.long_span==2*s && live.long_loss==2*m);
  // 临时忙允许跨过一个未知点；断层过大、Reset及迁移仍不可跨越。
  history.Clear();history.Push(Frame(98*s,10*m));history.Push(Frame(99*s,0,false));
  assert(history.Check(current,current.zones.at(128)).known);
  history.Clear();history.Push(Frame(97*s,10*m));assert(!history.Check(current,current.zones.at(128)).known);
  history.Clear();auto old=Frame(99*s,9*m);old.zones.at(128).device_generation=2;history.Push(old);
  assert(!history.Check(current,current.zones.at(128)).known);
  history.Clear();old=Frame(99*s,9*m);old.before.zones[128].sequence=0;history.Push(old);
  current.after.zones[128].sequence=2;current.after.zones[128].last_end_ns=99*s;
  assert(!history.Check(current,current.zones.at(128)).known);
  // 不等候Greedy建议，仍按净复制/回收量直接排序；覆盖与空间资格保持原样。
  b.snapshot_ns=100*s;b.flags=7;b.rc=1000;b.delta10=240;b.delta30=480;
  rrzc_simple::Candidate cold{256,7*m,128*m,0,1024,true,true,true};
  auto ranked=Rank({cold,c},{{c.zone,b}},{{c.zone,f}},1024,0,10*s);
  assert(ranked.front().candidate.zone==c.zone);
  c.covered=false;assert(Rank({c},{{c.zone,b}},{{c.zone,f}},1024,0,10*s).empty());
  std::cout<<"窗口自适应评分、短历史、停止读取、Reset/迁移与溢出边界通过；不声称真实RR收益\n";
}
