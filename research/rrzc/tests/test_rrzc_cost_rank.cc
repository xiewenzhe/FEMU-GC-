// 合成数据只验证排序与安全边界，不代表设备收益。
#include "../model/rrzc_cost_rank.h"
#include <cassert>
#include <iostream>
static unsigned checks=0;
#define CHECK(x) do { ++checks; assert(x); } while (0)
int main() {
  using namespace rrzc_v1;
  constexpr uint64_t m=1ULL<<20;
  const rrzc_guard::Mode execute{true,true,true},shadow{true,true,false};
  CHECK(rrzc_rank::Enabled("1",execute,false));
  CHECK(!rrzc_rank::Enabled(nullptr,execute,false));
  CHECK(!rrzc_rank::Enabled("0",execute,false));
  CHECK(!rrzc_rank::Enabled("01",execute,false));
  CHECK(!rrzc_rank::Enabled("1",shadow,false));
  CHECK(!rrzc_rank::Enabled("1",execute,true));
  std::vector<rrzc_simple::Candidate> c={
    {1,16*m,256*m,0,0,true,true,false},
    {2,24*m,256*m,0,0,true,true,false},
    {3,32*m,256*m,0,0,true,true,false}};
  std::map<uint64_t,RecentBlock> b;
  std::map<uint64_t,GrowthEvidence> e;
  for (unsigned z=1;z<=3;++z) {
    b[z].zone=z;b[z].rc=3900;b[z].flags=kEnabled;
    e[z].known=true;e[z].delta=32;e[z].predicted_ns=(z==1?100:z==2?8:4)*kSecond;
  }
  auto run=[&](bool enabled=true,uint64_t threshold=4096,uint64_t horizon=10*kSecond,uint64_t budget=16ULL<<20) {
    return rrzc_rank::Evaluate(enabled,c,b,e,threshold,192*m,horizon,budget);
  };
  CHECK(run(false).proposed_zone==3);
  CHECK(run().proposed_zone==2 && run().extra_valid==8*m && run().predicted_ns==8*kSecond);
  e[2].predicted_ns=11*kSecond;CHECK(run().proposed_zone==3);e[2].predicted_ns=8*kSecond;
  e[2].known=false;CHECK(run().proposed_zone==3);e[2].known=true;
  c[1].covered=false;CHECK(run().proposed_zone==3);c[1].covered=true;
  c[1].target_available=false;CHECK(run().proposed_zone==3);c[1].target_available=true;
  CHECK(!run(true,4096,10*kSecond,7*m).would_switch);
  CHECK(run(true,4096,10*kSecond,8*m).proposed_zone==2);
  e[1].known=false;CHECK(!run().would_switch);e[1].known=true;
  e[1].predicted_ns=5*kSecond;CHECK(!run().would_switch);e[1].predicted_ns=100*kSecond;
  CHECK(!run(true,0).would_switch);CHECK(!run(true,4096,0).would_switch);
  c[2].valid=24*m;CHECK(run().proposed_zone==3);
  e[2].predicted_ns=4*kSecond;CHECK(run().proposed_zone==2);
  c[2].valid=32*m;
  // 大整数交叉乘积及不同容量，不能只比较有效数据的绝对值。
  rrzc_simple::Candidate a,d;a.valid=UINT64_MAX/2;a.capacity=UINT64_MAX;
  d.valid=UINT64_MAX/3;d.capacity=UINT64_MAX;CHECK(rrzc_rank::LessCost(d,a));
  a.valid=32*m;a.capacity=512*m;d.valid=24*m;d.capacity=256*m;
  CHECK(rrzc_rank::LessCost(a,d));
  // 默认关闭逐项与原选择器保持一致；未知／低速／已达阈值均覆盖。
  for(unsigned x=0;x<40;++x) for(unsigned y=0;y<40;++y) {
    e[2].predicted_ns=x*kSecond;e[3].predicted_ns=y*kSecond;
    auto old=EvaluateShortShadow(c,b,e,4096,192*m,10*kSecond,16*m),off=run(false);
    CHECK(old.proposed_zone==off.proposed_zone && old.would_switch==off.would_switch &&
          old.predicted_ns==off.predicted_ns && old.extra_valid==off.extra_valid);
  }
  // 尾部和源复核仍使用原Apply，排序模块不替代这些检查。
  e[2].predicted_ns=8*kSecond;e[3].predicted_ns=4*kSecond;
  rrzc_cost::ReserveObservation reserve;reserve.enabled=reserve.known=reserve.present=true;
  auto proposal=run();rrzc_guard::TailDecision tail;tail.allow=false;
  auto actual=rrzc_guard::Apply(proposal.greedy,proposal,c,192*m,16*m,tail,reserve,reserve,
      [](const rrzc_simple::Candidate&){return true;});
  CHECK(!actual.switched && actual.actual.selected.zone==1);
  tail.allow=true;
  actual=rrzc_guard::Apply(proposal.greedy,proposal,c,192*m,16*m,tail,reserve,reserve,
      [](const rrzc_simple::Candidate&){return false;});
  CHECK(!actual.switched && actual.actual.selected.zone==1);
  std::cout<<"成本排序："<<checks<<"项合成检查通过。\n";
}
