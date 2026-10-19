// 合成数据只验证边界与选择逻辑，不代表SSD实验收益。
#include "rrzc_retention_selector.h"
#include <cassert>
#include <iostream>

int main() {
  using namespace rrzc_retention;
  constexpr uint64_t mib=1ULL<<20;
  uint64_t now=kSecond;
  rrzc_observe::MigrationWindow tracker(true,[&]{return now;});
  auto frame=[&](unsigned second,uint64_t valid) {
    now=second*kSecond;
    Frame f; f.session_hi=1; f.before=tracker.Freeze();
    f.zones[1]={1,now,valid,0,0,0,true}; f.after=tracker.Freeze(); return f;
  };
  History history;
  for (unsigned s=1;s<=30;++s) history.Push(frame(s,(100-s)*mib));
  auto current=frame(31,69*mib);
  auto f=history.Check(current,current.zones.at(1),10*kSecond);
  assert(f.known && f.loss10==10*mib && f.loss30==30*mib && f.retained==59*mib);
  auto bad=current; bad.zones[1].device_generation++;
  assert(!history.Check(bad,bad.zones.at(1),10*kSecond).known);
  bad=current; bad.zones[1].host_sequence++;
  assert(!history.Check(bad,bad.zones.at(1),10*kSecond).known);
  bad=current; bad.session_hi++;
  assert(!history.Check(bad,bad.zones.at(1),10*kSecond).known);
  bad=current; bad.zones[1].known=false;
  assert(!history.Check(bad,bad.zones.at(1),10*kSecond).known);
  assert(!history.Check(current,current.zones.at(1),0).known);
  now=31*kSecond; { auto scope=tracker.Mark({1}); }
  current.after=tracker.Freeze();
  assert(!history.Check(current,current.zones.at(1),10*kSecond).known);
  // 采样缺口、增加、未知忙点都不能用端点净差掩盖。
  for (unsigned kind=0;kind<3;++kind) {
    History h;
    for (unsigned s=32;s<=62;++s) {
      if (kind==0 && s>=40 && s<=42) continue;
      auto p=frame(s,(100-s)*mib);
      if (s==45 && kind==1) p.zones[1].valid=mib;
      if (s==45 && kind==2) p.zones[1].known=false;
      h.Push(p);
    }
    auto c=frame(63,37*mib);
    assert(!h.Check(c,c.zones.at(1),10*kSecond).known);
  }
  std::vector<rrzc_simple::Candidate> candidates={
      {1,16*mib,256*mib,0,0,true,true,false},
      {2,32*mib,256*mib,0,0,true,true,false},
      {3,24*mib,256*mib,0,0,true,true,false}};
  std::map<uint64_t,rrzc_v1::RecentBlock> usable;
  std::map<uint64_t,rrzc_v1::GrowthEvidence> growth;
  for (unsigned z=1;z<=3;++z) {
    usable[z].zone=z; usable[z].flags=rrzc_v1::kEnabled;
    usable[z].rc=z==1 ? 100 : 4096;
    growth[z].known=true; growth[z].predicted_ns=z==1 ? UINT64_MAX : 0;
  }
  std::map<uint64_t,Forecast> forecasts;
  forecasts[2].known=true; forecasts[2].retained=32*mib;
  forecasts[3].known=true; forecasts[3].retained=24*mib;
  auto evaluate=[&]{return Evaluate(candidates,usable,growth,forecasts,4096,192*mib,10*kSecond,16*mib);};
  assert(evaluate().proposed_zone==3);
  forecasts[3].retained=15*mib;
  assert(evaluate().proposed_zone==2);
  forecasts[2].retained=16*mib;
  assert(evaluate().proposed_zone==3);
  forecasts[3].retained=8*mib;
  assert(!evaluate().would_switch);
  forecasts[2].retained=32*mib; forecasts[2].known=false;
  assert(!evaluate().would_switch);
  forecasts[2].known=true; candidates[1].valid=33*mib;
  assert(!evaluate().would_switch);
  candidates[1].valid=32*mib; usable[1].rc=4096;
  assert(!evaluate().would_switch);
  usable[1].rc=100; growth[1].known=false;
  assert(!evaluate().would_switch);
  std::cout<<"留存收益：连续历史、代际、迁移污染、未知回退、预算及收益排序检查通过。\n";
}
