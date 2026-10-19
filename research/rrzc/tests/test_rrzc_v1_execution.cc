// 合成候选只验证真实选区接线及保守回退，不是SSD性能或机制收益实验。
#include "../model/rrzc_v1_execution.h"
#include <cassert>
#include <iostream>
#include <set>

static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)

int main() {
  using namespace rrzc_v1;
  CHECK(!ResolveMode(nullptr,nullptr,true,false).observe);
  CHECK(!ResolveMode(nullptr,"0",true,false).execute);
  CHECK(!ResolveMode(nullptr,"错误",true,false).execute);
  CHECK(!ResolveMode(nullptr,"01",true,false).execute);
  CHECK(ResolveMode("1",nullptr,true,false).observe);
  CHECK(!ResolveMode("1",nullptr,true,false).execute);
  CHECK(ResolveMode(nullptr,"1",true,false).observe);
  CHECK(ResolveMode(nullptr,"1",true,false).execute);
  CHECK(ResolveMode("0","1",true,false).execute);
  CHECK(!ResolveMode("1","1",true,false).observe);
  CHECK(!ResolveMode(nullptr,"1",false,false).execute);
  CHECK(!ResolveMode(nullptr,"1",true,true).execute);
  constexpr uint64_t mib=1ULL<<20;
  const uint64_t budget=192*mib, extra_budget=16*mib;
  std::vector<rrzc_simple::Candidate> candidates={
      {1,16*mib,256*mib,0,0,true,true,false},
      {2,32*mib,256*mib,0,0,true,true,false}};
  RecentBlock cold, hot;
  cold.rc=100; cold.flags=kEnabled|kValid10|kValid30;
  hot=cold; hot.rc=4000; hot.delta10=200; hot.delta30=600;
  std::map<uint64_t,RecentBlock> recent={{1,cold},{2,hot}};
  const auto greedy=rrzc_simple::Choose(candidates,budget,false);
  auto proposal=Evaluate(candidates,recent,4096,budget,10*kSecond,extra_budget);
  CHECK(proposal.would_switch && proposal.proposed_zone==2);
  unsigned validated=0;
  uint64_t stale_zone=0;
  auto validate=[&](const rrzc_simple::Candidate& c) {
    ++validated; return c.zone!=stale_zone;
  };
  auto apply=[&](bool enabled=true) {
    validated=0;
    return ApplyDecision(greedy,proposal,candidates,budget,extra_budget,enabled,validate);
  };
  auto result=apply(false);
  CHECK(!result.switched && result.actual.selected.zone==1 && validated==0);
  result=apply();
  CHECK(result.switched && result.actual.found && result.actual.selected.zone==2);
  CHECK(result.extra_valid==extra_budget && validated==2);
  CHECK(!result.actual.used_weighted);
  // 同一份结果不能同时保留Greedy和建议区，避免变回批量迁移。
  std::set<uint64_t> selected={1,2};
  selected.clear(); selected.emplace(result.actual.selected.zone);
  CHECK(selected.size()==1 && *selected.begin()==2);
  stale_zone=1; result=apply();
  CHECK(!result.switched && result.actual.selected.zone==1 && validated==1);
  CHECK(result.extra_valid==0);
  stale_zone=2; result=apply();
  CHECK(!result.switched && result.actual.selected.zone==1 && validated==2);
  stale_zone=0;
  auto good=proposal;
  proposal.greedy.selected.zone=9;
  CHECK(!apply().switched && validated==0);
  proposal=good; proposal.greedy.selected.valid+=1;
  CHECK(!apply().switched);
  proposal=good; proposal.proposed_zone=99;
  CHECK(!apply().switched);
  proposal=good; proposal.proposed_zone=1;
  CHECK(!apply().switched);
  proposal=good; proposal.extra_valid=0;
  CHECK(!apply().switched);
  proposal=good; candidates[1].valid+=1;
  CHECK(!apply().switched);
  candidates[1].valid-=1; candidates[1].covered=false;
  CHECK(!apply().switched);
  candidates[1].covered=true; candidates[1].target_available=false;
  CHECK(!apply().switched);
  candidates[1].target_available=true;
  recent[2].flags=kEnabled;
  proposal=Evaluate(candidates,recent,4096,budget,10*kSecond,extra_budget);
  result=apply(); CHECK(!result.switched && validated==0);
  recent[2]=hot; recent[1]=hot;
  proposal=Evaluate(candidates,recent,4096,budget,10*kSecond,extra_budget);
  result=apply(); CHECK(!result.switched && result.actual.selected.zone==1);
  recent[1]=cold;
  proposal=Evaluate(candidates,recent,4096,budget,10*kSecond,extra_budget-1);
  CHECK(!apply().switched);
  proposal=good;
  result=ApplyDecision({},proposal,candidates,budget,extra_budget,true,validate);
  CHECK(!result.actual.found && !result.switched);
  result=ApplyDecision(greedy,proposal,candidates,budget,extra_budget-1,true,validate);
  CHECK(!result.switched && result.actual.selected.zone==1);
  std::cout<<"V1真实选区接线："<<checks<<"项检查通过（合成数据）\n";
}
