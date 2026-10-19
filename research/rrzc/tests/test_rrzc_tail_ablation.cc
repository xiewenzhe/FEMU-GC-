// 合成状态只验证消融边界，不代表设备行为或净收益。
#include "../model/rrzc_tail_ablation.h"
#include <cassert>
#include <iostream>
static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
int main() {
  using namespace rrzc_tail_ablation;
  const auto execute=rrzc_guard::Resolve("execute",true,false,false);
  const auto shadow=rrzc_guard::Resolve("shadow",true,false,false);
  CHECK(!Resolve(nullptr,execute).requested);
  CHECK(!Resolve("0",execute).enabled);
  CHECK(Resolve("keep",execute).enabled && !Resolve("keep",execute).bypass);
  CHECK(Resolve("bypass",execute).enabled && Resolve("bypass",execute).bypass);
  for(const char* value:{"","off","on","bad","1"}) {
    auto mode=Resolve(value,execute);
    CHECK(mode.requested && !mode.enabled && !mode.bypass);
  }
  for(const auto& disabled:{rrzc_guard::Mode{},shadow,
      rrzc_guard::Resolve("execute",false,false,false),
      rrzc_guard::Resolve("execute",true,true,false),
      rrzc_guard::Resolve("execute",true,false,true)}) {
    CHECK(!Resolve("keep",disabled).enabled);
    CHECK(!Resolve("bypass",disabled).enabled);
  }
  const uint64_t mib=1ULL<<20,cap=256*mib,budget=192*mib,extra=16*mib;
  std::vector<rrzc_simple::Candidate> candidates={
    {1,16*mib,cap,0,4096,true,true,true},{2,32*mib,cap,3900,4096,true,true,true}};
  rrzc_v1::RecentBlock cold,hot;cold.zone=1;cold.flags=rrzc_v1::kEnabled;
  hot=cold;hot.zone=2;hot.rc=3900;
  std::map<uint64_t,rrzc_v1::RecentBlock> usable={{1,cold},{2,hot}};
  rrzc_v1::GrowthEvidence quiet,rising;quiet.known=true;rising.known=true;
  rising.delta=32;rising.predicted_ns=6125000000ULL;
  std::map<uint64_t,rrzc_v1::GrowthEvidence> growth={{1,quiet},{2,rising}};
  auto proposal=rrzc_v1::EvaluateShortShadow(candidates,usable,growth,4096,budget,10*rrzc_v1::kSecond,extra);
  const auto greedy=rrzc_simple::Choose(candidates,budget,false);
  CHECK(proposal.would_switch && proposal.proposed_zone==2 && proposal.extra_valid==extra);
  rrzc_cost::ReserveObservation before;
  before.enabled=before.known=before.present=true;before.zone=3*cap;
  before.remaining=16*mib;before.valid=240*mib;before.wp=before.zone+240*mib;
  rrzc_guard::Bounds gb,cb;gb.Add(16*mib,512,8,cap);cb.Add(32*mib,512,8,cap);
  const auto original=rrzc_guard::Evaluate(gb,16*mib,cb,32*mib,before,cap);
  CHECK(!original.allow && !std::strcmp(original.category,"未知回退"));
  unsigned validations=0;
  auto apply=[&](const Mode& mode,const rrzc_cost::ReserveObservation& after,bool source=true) {
    validations=0;
    const auto tail=EffectiveTail(original,mode,proposal.would_switch);
    return rrzc_guard::Apply(greedy,proposal,candidates,budget,extra,tail,before,after,
      [&](const rrzc_simple::Candidate&){++validations;return source;});
  };
  const auto keep=Resolve("keep",execute),bypass=Resolve("bypass",execute);
  CHECK(!apply(Mode{},before).switched && validations==0);
  CHECK(!apply(keep,before).switched && validations==0);
  CHECK(apply(bypass,before).switched && validations==2);
  CHECK(apply(bypass,before).actual.selected.zone==2 && apply(bypass,before).extra_valid==extra);
  CHECK(!original.allow);  // 旁路不改写原诊断。
  CHECK(!apply(bypass,before,false).switched && validations==1);
  for(unsigned i=0;i<9;++i) {
    auto changed=before;
    switch(i) {
      case 0:changed.enabled=false;break;case 1:changed.known=false;break;
      case 2:changed.present=false;break;case 3:changed.zone++;break;
      case 4:changed.remaining++;break;case 5:changed.valid++;break;
      case 6:changed.wp++;break;case 7:changed.host_generation++;break;
      default:changed.host_sequence++;break;
    }
    CHECK(!apply(bypass,changed).switched);
  }
  candidates[1].covered=false;CHECK(!apply(bypass,before).switched);candidates[1].covered=true;
  candidates[1].target_available=false;CHECK(!apply(bypass,before).switched);candidates[1].target_available=true;
  proposal.extra_valid++;CHECK(!apply(bypass,before).switched);proposal.extra_valid--;
  auto changed=proposal;changed.would_switch=false;proposal=changed;
  CHECK(!apply(bypass,before).switched);
  CHECK(!EffectiveTail(original,bypass,false).allow);
  const rrzc_guard::TailDecision allowed{true,"尾部不否决","合成可容纳"};
  CHECK(EffectiveTail(allowed,keep,true).allow);
  CHECK(!std::strcmp(EffectiveTail(allowed,keep,true).category,allowed.category));
  CHECK(!std::strcmp(EffectiveTail(original,Mode{},true).category,original.category));
  growth[2].delta=0;growth[2].predicted_ns=UINT64_MAX;
  CHECK(!rrzc_v1::EvaluateShortShadow(candidates,usable,growth,4096,budget,10*rrzc_v1::kSecond,extra).would_switch);
  growth[2].delta=32;growth[2].predicted_ns=11*rrzc_v1::kSecond;
  CHECK(!rrzc_v1::EvaluateShortShadow(candidates,usable,growth,4096,budget,10*rrzc_v1::kSecond,extra).would_switch);
  std::cout<<"尾部消融："<<checks<<"项检查通过（合成数据）\n";
}
