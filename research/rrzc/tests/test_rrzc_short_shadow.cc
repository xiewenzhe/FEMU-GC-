// 合成快照只检验独立短期影子准入，不是实际SSD收益数据。
#include "../model/rrzc_short_shadow.h"
#include "../model/rrzc_v1_execution.h"
#include <cassert>
#include <iostream>

static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
int main() {
  using namespace rrzc_v1;
  CHECK(!ShortShadowRequested(nullptr)); CHECK(!ShortShadowRequested("0"));
  CHECK(!ShortShadowRequested("01")); CHECK(ShortShadowRequested("1"));
  CHECK(ShortShadowEnabled("1",true,false,true));
  CHECK(!ShortShadowEnabled("1",false,false,true));
  CHECK(!ShortShadowEnabled("1",true,true,true));
  CHECK(!ShortShadowEnabled("1",true,false,false));
  CHECK(!ShortShadowEnabled(nullptr,true,false,true));
  CHECK(ShortShadowConflict("1","1")); CHECK(!ShortShadowConflict("1","0"));
  CHECK(!ShortShadowConflict(nullptr,"1"));
  const auto conflict=ResolveMode(nullptr,"1",true,ShortShadowConflict("1","1"));
  CHECK(!conflict.observe && !conflict.execute);
  constexpr uint64_t mib=1ULL<<20;
  const uint64_t budget=192*mib, extra_budget=16*mib;
  std::vector<rrzc_simple::Candidate> candidates={
      {1,16*mib,256*mib,0,0,true,true,false},
      {2,32*mib,256*mib,0,0,true,true,false}};
  RecentBlock cold,hot;
  cold.zone=1; cold.zone_index=1; cold.snapshot_ns=70*kSecond;
  cold.rc=480; cold.flags=kEnabled|kValid10|kValid30;
  hot=cold; hot.zone=2; hot.zone_index=2; hot.rc=3900; hot.delta10=180; hot.delta30=270;
  RecentReport now; now.session_hi=1; now.threshold=4096; now.blocks_per_zone=64; now.zones={cold,hot};
  auto old=now;
  for (auto& b:old.zones) b.snapshot_ns-=kSecond;
  old.zones[1].rc-=32;
  GrowthHistory history; history.Push(old);
  std::map<uint64_t,RecentBlock> usable={{1,cold},{2,hot}};
  std::map<uint64_t,GrowthEvidence> evidence;
  auto refresh=[&] {
    evidence.clear();
    for (const auto& p:usable) evidence[p.first]=history.Check(now,p.second,10*kSecond);
  };
  refresh();
  auto evaluate=[&](uint64_t extra=16ULL<<20) {
    return EvaluateShortShadow(candidates,usable,evidence,4096,budget,10*kSecond,extra);
  };
  CHECK(!Evaluate(candidates,usable,4096,budget,10*kSecond,extra_budget).would_switch);
  auto out=evaluate();
  CHECK(out.would_switch && out.proposed_zone==2 && out.greedy.selected.zone==1);
  CHECK(out.extra_valid==extra_budget && out.predicted_ns==6125000000ULL);
  // 旧窗口完整性只作为并列诊断，不是短期计数有效性的替代条件。
  usable[2].flags=kEnabled; out=evaluate(); CHECK(out.would_switch);
  usable[2]=hot; evidence[2].known=false;
  CHECK(!evaluate().would_switch); refresh();
  evidence[1].known=false; CHECK(!evaluate().would_switch); refresh();
  evidence.erase(1); CHECK(!evaluate().would_switch); refresh();
  usable.erase(1); CHECK(!evaluate().would_switch); usable[1]=cold; refresh();
  CHECK(!evaluate(extra_budget-1).would_switch);
  candidates[1].covered=false; CHECK(!evaluate().would_switch); candidates[1].covered=true;
  candidates[1].target_available=false; CHECK(!evaluate().would_switch); candidates[1].target_available=true;
  candidates[1].valid=80*mib; CHECK(!evaluate().would_switch); candidates[1].valid=32*mib;
  // 已停读但RC很高不能靠旧突发量获准。
  old.zones[1].rc=hot.rc; history.Clear(); history.Push(old); refresh();
  CHECK(!evaluate().would_switch);
  old.zones[1].rc=hot.rc-4; history.Clear(); history.Push(old); refresh();
  CHECK(!evaluate().would_switch);
  old.zones[1].block_id=8; history.Clear(); history.Push(old); refresh();
  CHECK(!evaluate().would_switch);
  old=now; for (auto& b:old.zones) b.snapshot_ns-=kSecond;
  old.zones[1].rc-=32; old.zones[1].generation++;
  history.Clear(); history.Push(old); refresh(); CHECK(!evaluate().would_switch);
  old.zones[1].generation--; old.session_hi++;
  history.Clear(); history.Push(old); refresh(); CHECK(!evaluate().would_switch);
  old.session_hi--; history.Clear(); history.Push(old); refresh();
  // Greedy本身紧迫时仍选Greedy；已达阈值且同块已知即使增量0也不误判为安全。
  usable[1].rc=4096; evidence[1].known=true; evidence[1].delta=0;
  out=evaluate(); CHECK(!out.would_switch && out.proposed_zone==1 && out.predicted_ns==0);
  usable[1]=cold; refresh(); usable[2].rc=4096; evidence[2].delta=0;
  out=evaluate(); CHECK(out.would_switch && out.predicted_ns==0);
  evidence[2].known=false; CHECK(!evaluate().would_switch); usable[2]=hot; refresh();
  CHECK(!EvaluateShortShadow(candidates,usable,evidence,0,budget,10*kSecond,extra_budget).would_switch);
  CHECK(!EvaluateShortShadow(candidates,usable,evidence,4096,budget,0,extra_budget).would_switch);
  CHECK(!EvaluateShortShadow(candidates,usable,evidence,4096,budget,301*kSecond,extra_budget).would_switch);
  CHECK(!EvaluateShortShadow({},usable,evidence,4096,budget,10*kSecond,extra_budget).greedy.found);
  // 紧迫度并列时沿用较小有效量，再沿用较小Zone地址；不修改原候选资格。
  auto other=hot; other.zone=3; other.zone_index=3;
  candidates.push_back({3,24*mib,256*mib,0,0,true,true,false});
  usable[3]=other; evidence[3]=evidence[2];
  out=evaluate(); CHECK(out.would_switch && out.proposed_zone==3 && out.extra_valid==8*mib);
  candidates[2].valid=32*mib; out=evaluate(); CHECK(out.proposed_zone==2);
  evidence[2].predicted_ns=20*kSecond; out=evaluate(); CHECK(out.proposed_zone==3);
  std::cout<<"独立短期影子选择："<<checks<<"项检查通过（合成数据）\n";
}
