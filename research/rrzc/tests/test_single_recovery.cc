// 以下为合成边界测试，不代表设备实验结果。
#include "rrzc_single_recovery.h"
#include <cassert>
#include <iostream>
#include <atomic>
struct Zone { std::atomic<uint64_t> used_capacity_{0}; };
struct Extent { uint64_t start_,length_; Zone* zone_; };
int main() {
  constexpr uint64_t m=1ULL<<20;
  rrzc_simple::Candidate a,b;
  a.zone=1; a.capacity=256*m; a.valid=80*m; a.covered=a.target_available=true;
  b=a; b.zone=2; b.valid=90*m;
  auto r=rrzc_recovery::Choose({a,b},192*m);
  assert(r.needed && r.decision.found && r.decision.selected.zone==1);
  a.valid=64*m;
  assert(!rrzc_recovery::Choose({a,b},192*m).needed);
  a.covered=false;
  r=rrzc_recovery::Choose({a,b},192*m);
  assert(r.needed && r.decision.found && r.decision.selected.zone==2);
  // 真实失败现场：7852字节的非SST区不能阻止约64.4MiB的安全SST区。
  a.valid=7852;a.covered=false;b.valid=67534210;
  r=rrzc_recovery::Choose({a,b},192*m);
  assert(r.needed && r.decision.found && r.decision.selected.zone==2);
  b.valid=90*m;
  a.valid=80*m; a.covered=true; a.target_available=false;
  r=rrzc_recovery::Choose({a,b},192*m);
  assert(r.needed && r.decision.found && r.decision.selected.zone==2);
  b.target_available=false;
  r=rrzc_recovery::Choose({a,b},192*m);
  assert(r.needed && !r.decision.found);
  assert(rrzc_recovery::Choose({},192*m).needed);
  assert(!rrzc_recovery::Enabled(nullptr,true,true));
  assert(!rrzc_recovery::Enabled("1",false,true));
  assert(!rrzc_recovery::Enabled("1",true,false));
  assert(rrzc_recovery::Enabled("1",true,true));
  assert(rrzc_recovery::WaitAttempts(false)==200);
  assert(rrzc_recovery::WaitAttempts(true)==600);
  Zone source,target;source.used_capacity_=30;target.used_capacity_=12;
  Extent old1{1,12,&source},old2{2,18,&source};
  std::vector<Extent*> old{&old1,&old2};
  std::vector<Extent*> copied{new Extent{3,12,&target},new Extent{2,18,&source}};
  assert(rrzc_recovery::DiscardUncommitted(old,&copied));
  assert(copied.empty() && target.used_capacity_==0 && source.used_capacity_==30);
  copied={new Extent{1,12,&source}};
  assert(!rrzc_recovery::DiscardUncommitted(old,&copied));
  assert(source.used_capacity_==30);delete copied.front();
  std::cout<<"公共单区恢复边界检查通过\n";
}
