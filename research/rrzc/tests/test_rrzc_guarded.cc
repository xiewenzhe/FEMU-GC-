// 合成状态只核验入口、成本约束和复核；不是设备收益数据。
#include "../model/rrzc_guarded_selector.h"
#include <cassert>
#include <iostream>
static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
int main() {
  using namespace rrzc_guard;
  const uint64_t mib=1ULL<<20,cap=256*mib,budget=192*mib,extra=16*mib;
  CHECK(!Resolve(nullptr,true,false,false).requested);
  CHECK(!Resolve("off",true,false,false).requested);
  CHECK(!Resolve("0",true,false,false).requested);
  CHECK(Resolve("shadow",true,false,false).observe && !Resolve("shadow",true,false,false).execute);
  CHECK(Resolve("execute",true,false,false).execute);
  CHECK(Resolve("bad",true,false,false).requested && !Resolve("bad",true,false,false).observe);
  CHECK(!Resolve("execute",false,false,false).observe);
  CHECK(!Resolve("execute",true,true,false).observe);
  CHECK(!Resolve("execute",true,false,true).observe);
  auto bound=[&](std::initializer_list<uint64_t> lengths) {
    Bounds b; for(auto n:lengths) b.Add(n,512,8,cap); return b;
  };
  auto g=bound({16*mib}),c=bound({32*mib});
  rrzc_cost::ReserveObservation r;
  r.enabled=r.known=r.present=true; r.zone=3*cap; r.remaining=64*mib; r.valid=192*mib;
  r.wp=r.zone+cap-r.remaining;
  auto evaluate=[&]{return Evaluate(g,16*mib,c,32*mib,r,cap);};
  CHECK(g.Complete(16*mib) && c.Complete(32*mib));
  CHECK(g.first_upper==16*mib+512 && c.upper==32*mib+512);
  CHECK(evaluate().allow);
  r.remaining=16*mib; r.valid=240*mib;
  CHECK(!evaluate().allow && !std::strcmp(evaluate().category,"未知回退"));
  auto smaller=bound({15*mib});
  auto veto=Evaluate(smaller,15*mib,c,32*mib,r,cap);
  CHECK(!veto.allow && !std::strcmp(veto.category,"预留压力保守拦截"));
  r.valid=0; CHECK(evaluate().allow); r.valid=240*mib;
  r.known=false; CHECK(!evaluate().allow); r.known=true;
  r.present=false; CHECK(!evaluate().allow); r.present=true;
  r.enabled=false; CHECK(!evaluate().allow); r.enabled=true;
  r.valid=cap; CHECK(!evaluate().allow); r.valid=240*mib;
  CHECK(!Evaluate(g,1,c,32*mib,r,cap).allow);
  CHECK(!Evaluate(Bounds{},16*mib,c,32*mib,r,cap).allow);
  CHECK(!Evaluate(g,16*mib,bound({cap}),cap,r,cap).allow);
  CHECK(!Evaluate(g,16*mib,bound({8*mib,24*mib}),32*mib,r,cap).allow);
  CHECK(Evaluate(g,16*mib,bound({4*mib,8*mib}),12*mib,r,cap).allow);
  Bounds bad; bad.Add(UINT64_MAX,512,8,cap); CHECK(!bad.known);
  bad.Add(UINT64_MAX,512,8,cap); CHECK(!bad.known);
  CHECK(StableReserve(r,r));
  for(unsigned i=0;i<9;++i) {
    auto changed=r;
    switch(i) {
      case 0:changed.known=false;break; case 1:changed.present=false;break;
      case 2:changed.enabled=false;break; case 3:changed.zone++;break;
      case 4:changed.remaining++;break; case 5:changed.valid++;break;
      case 6:changed.wp++;break; case 7:changed.host_generation++;break;
      default:changed.host_sequence++;break;
    }
    CHECK(!StableReserve(r,changed));
  }
  std::vector<rrzc_simple::Candidate> candidates={
      {1,16*mib,cap,0,4096,true,true,true},{2,32*mib,cap,3900,4096,true,true,true}};
  rrzc_v1::RecentBlock cold,hot; cold.zone=1; cold.flags=rrzc_v1::kEnabled;
  hot=cold; hot.zone=2; hot.rc=3900;
  std::map<uint64_t,rrzc_v1::RecentBlock> usable={{1,cold},{2,hot}};
  rrzc_v1::GrowthEvidence quiet,rising; quiet.known=true; rising.known=true;
  rising.delta=32; rising.predicted_ns=6125000000ULL;
  std::map<uint64_t,rrzc_v1::GrowthEvidence> growth={{1,quiet},{2,rising}};
  auto proposal=rrzc_v1::EvaluateShortShadow(candidates,usable,growth,4096,budget,10*rrzc_v1::kSecond,extra);
  auto greedy=rrzc_simple::Choose(candidates,budget,false);
  CHECK(proposal.would_switch && proposal.proposed_zone==2 && proposal.extra_valid==extra);
  r.remaining=64*mib; r.valid=192*mib; auto tail=evaluate();
  auto apply=[&](const rrzc_cost::ReserveObservation& after,bool source=true) {
    return Apply(greedy,proposal,candidates,budget,extra,tail,r,after,
        [&](const rrzc_simple::Candidate&){return source;});
  };
  CHECK(apply(r).switched && apply(r).actual.selected.zone==2);
  CHECK(!apply(r,false).switched && apply(r,false).actual.selected.zone==1);
  auto changed=r; changed.host_generation++; CHECK(!apply(changed).switched);
  changed=r; changed.remaining--; CHECK(!apply(changed).switched);
  tail={false,"未知回退","合成未知"}; CHECK(!apply(r).switched);
  tail=evaluate(); proposal.extra_valid++; CHECK(!apply(r).switched); proposal.extra_valid--;
  candidates[1].covered=false; CHECK(!apply(r).switched); candidates[1].covered=true;
  growth[2].delta=0; growth[2].predicted_ns=UINT64_MAX;
  CHECK(!rrzc_v1::EvaluateShortShadow(candidates,usable,growth,4096,budget,10*rrzc_v1::kSecond,extra).would_switch);
  std::cout<<"独立短期尾部约束："<<checks<<"项检查通过（合成数据）\n";
}
