// 合成测试只核验收益预算和原执行护栏，不代表设备节省。
#include "rrzc_benefit_budget.h"
#include "rrzc_guarded_selector.h"
#include <cassert>
#include <iostream>

int main() {
  constexpr uint64_t mib=1ULL<<20,second=rrzc_v1::kSecond;
  using namespace rrzc_benefit_budget;
  std::vector<rrzc_simple::Candidate> cs={
    {1,10*mib,256*mib,0,4096,true,true,true},
    {2,40*mib,256*mib,0,4096,true,true,true}};
  std::map<uint64_t,rrzc_v1::RecentBlock> blocks;
  std::map<uint64_t,rrzc_v1::GrowthEvidence> growth;
  std::map<uint64_t,rrzc_retention::Forecast> forecasts;
  for (uint64_t z=1;z<=2;++z) {
    auto& b=blocks[z];b.zone=z;b.rc=z==1?100:4000;b.flags=7;
    b.delta10=z==1?0:960;b.delta30=z==1?0:2880;
    auto& g=growth[z];g.known=true;g.delta=z==1?0:96;g.interval_ns=second;
    g.predicted_ns=z==1?UINT64_MAX:second;
    auto& f=forecasts[z];f.known=true;f.retained=cs[z-1].valid;
    f.interval10=10*second;f.interval30=30*second;
  }
  auto evaluate=[&]{return rrzc_benefit_budget::Evaluate(cs,blocks,growth,forecasts,4096,192*mib,10*second,16*mib);};
  const auto legacy=rrzc_net_cost::Evaluate(cs,blocks,growth,forecasts,4096,192*mib,10*second,16*mib);
  assert(!legacy.decision.would_switch);
  auto report=evaluate();auto proposal=report.net.decision;
  assert(proposal.would_switch && proposal.extra_valid==30*mib && report.guard_budget==30*mib);
  assert(!rrzc_net_cost::Evaluate(cs,blocks,growth,forecasts,4096,192*mib,10*second,16*mib).decision.would_switch);
  // 相同候选、来源与尾部复核；预算须一致传递，不能被执行层再次按16MiB拒绝。
  rrzc_cost::ReserveObservation reserve;reserve.enabled=reserve.known=reserve.present=true;
  reserve.zone=9;reserve.remaining=128*mib;reserve.valid=128*mib;reserve.wp=128*mib;
  rrzc_guard::TailDecision tail{true,"尾部不否决","测试已确认可容纳"};
  auto apply=[&](uint64_t budget,bool valid) {
    return rrzc_guard::Apply(proposal.greedy,proposal,cs,192*mib,budget,tail,reserve,reserve,
        [&](const rrzc_simple::Candidate&){return valid;});
  };
  assert(!apply(16*mib,true).switched);
  assert(apply(report.guard_budget,true).switched);
  assert(!apply(report.guard_budget,false).switched);
  tail.allow=false;assert(!apply(report.guard_budget,true).switched);tail.allow=true;
  auto changed=reserve;++changed.wp;
  assert(!rrzc_guard::Apply(proposal.greedy,proposal,cs,192*mib,report.guard_budget,tail,
      reserve,changed,[](const rrzc_simple::Candidate&){return true;}).switched);
  ++proposal.extra_valid;assert(!apply(report.guard_budget,true).switched);--proposal.extra_valid;
  // 无收益、历史未知、空间资格不足、Greedy未知或紧迫时均回退。
  growth[2].predicted_ns=10*second;assert(!evaluate().net.decision.would_switch);
  growth[2].predicted_ns=second;
  forecasts[2].known=false;assert(!evaluate().net.decision.would_switch);forecasts[2].known=true;
  cs[1].valid=65*mib;assert(!evaluate().net.decision.would_switch);cs[1].valid=40*mib;
  cs[1].target_available=false;assert(!evaluate().net.decision.would_switch);cs[1].target_available=true;
  growth[1].known=false;assert(!evaluate().net.decision.would_switch);growth[1].known=true;
  blocks[1].rc=4096;assert(!evaluate().net.decision.would_switch);blocks[1].rc=100;
  cs[1].capacity=257*mib;assert(!evaluate().net.decision.would_switch);cs[1].capacity=256*mib;
  // 收益预算严格不等式、零收益、字段矛盾和大整数边界。
  auto cost=report.net.costs.at(2);
  const auto allowed=CreditAllowance(cs[0],cs[1],cost);assert(allowed.known);
  const __uint128_t rhs=__uint128_t(cost.credit)*(cs[0].capacity-cs[0].valid);
  assert(__uint128_t(allowed.bytes)*cs[0].capacity<rhs);
  assert(__uint128_t(allowed.bytes+1)*cs[0].capacity>=rhs);
  cost.credit=0;assert(!CreditAllowance(cs[0],cs[1],cost).known);
  cost=report.net.costs.at(2);++cost.net_copy;assert(!CreditAllowance(cs[0],cs[1],cost).known);
  auto a=cs[0],b=cs[1];a.capacity=b.capacity=UINT64_MAX;
  a.valid=UINT64_MAX/8;b.valid=UINT64_MAX/4;
  cost.known=true;cost.credit=b.valid;cost.net_copy=0;cost.reclaimed=b.capacity-b.valid;
  assert(CreditAllowance(a,b,cost).known);
  assert(Parse(nullptr,true,false,false,false,false,false)==Mode::Off);
  assert(Parse("observe",true,false,false,false,false,false)==Mode::Observe);
  assert(Parse("execute",true,true,false,false,false,false)==Mode::Execute);
  assert(Parse("observe",true,true,false,false,false,false)==Mode::Off);
  assert(Parse("execute",true,false,false,false,false,false)==Mode::Off);
  for (int conflict=0;conflict<4;++conflict)
    assert(Parse("execute",true,true,conflict==0,conflict==1,conflict==2,conflict==3)==Mode::Off);
  assert(Parse("execute",false,true,false,false,false,false)==Mode::Off);
  assert(Parse("invalid",true,true,false,false,false,false)==Mode::Off);
  std::cout<<"收益预算：原资格、默认关闭、严格成本、预算传递及源尾部拒绝边界通过\n";
}
