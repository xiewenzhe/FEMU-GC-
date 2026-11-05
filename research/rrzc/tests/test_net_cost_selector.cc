// 合成场景只验证模型单调性、退化和安全边界，不是性能实验。
#include "rrzc_net_cost_selector.h"
#include <cassert>
#include <iostream>

int main() {
  using namespace rrzc_net_cost;
  constexpr uint64_t mib=1ULL<<20,H=10*kSecond;
  std::vector<rrzc_simple::Candidate> cs={
    {1,16*mib,256*mib,0,4096,true,true,true},
    {2,24*mib,256*mib,0,4096,true,true,true},
    {3,24*mib,256*mib,0,4096,true,true,true}};
  std::map<uint64_t,rrzc_v1::RecentBlock> blocks;
  std::map<uint64_t,rrzc_v1::GrowthEvidence> growth;
  std::map<uint64_t,rrzc_retention::Forecast> history;
  for (unsigned z=1;z<=3;++z) {
    auto& b=blocks[z]; b.zone=z; b.flags=7; b.rc=z==1?100:4000;
    b.delta10=z==1?0:960; b.delta30=z==1?0:2880;
    auto& g=growth[z];g.known=true;g.delta=z==1?0:96;g.interval_ns=kSecond;
    g.predicted_ns=z==1?UINT64_MAX:kSecond;
    auto& f=history[z];f.known=true;f.retained=cs[z-1].valid;
    f.interval10=10*kSecond;f.interval30=30*kSecond;
  }
  auto evaluate=[&]{return rrzc_net_cost::Evaluate(cs,blocks,growth,history,4096,192*mib,H,16*mib);};
  auto estimate=[&]{return Estimate(cs[1],blocks[2],growth[2],history[2],4096,H);};
  auto first=evaluate();assert(first.decision.would_switch && first.decision.proposed_zone==2);
  const auto base=estimate();assert(base.known && base.retained==24*mib);
  assert(base.credit==uint64_t(__uint128_t(24*mib)*9/10));
  // 原模型在无退役时评分相抵消；V2相同有效量可区分不同达阈值时间。
  growth[2].predicted_ns=9*kSecond;
  assert(evaluate().decision.proposed_zone==3);
  const auto slow=estimate();assert(slow.credit<base.credit && slow.net_copy>base.net_copy);
  growth[2].predicted_ns=kSecond;
  // 近期退役越快抵扣越少；长期零退役不能掩盖最新退役突发。
  history[2].loss10=history[2].loss30=10*mib;
  const auto decay=estimate();assert(decay.retained==23*mib && decay.credit<base.credit);
  assert(evaluate().decision.proposed_zone==3);
  history[2].loss10=history[2].loss30=0;
  // 相同复制成本，回收空间越少成本越高；交叉相乘不依赖浮点。
  Cost a=base,b=base;b.reclaimed/=2;assert(Less(a,b) && !Less(b,a));
  // 长期和短期读数预测冲突时，不只采用瞬时突发。
  blocks[2].delta10=1;blocks[2].delta30=3;
  assert(!estimate().known && evaluate().decision.proposed_zone==3);
  blocks[2].delta10=960;blocks[2].delta30=2880;
  growth[2].predicted_ns=H;assert(estimate().known && estimate().credit==0);
  growth[2].predicted_ns=H+1;assert(!estimate().known);
  growth[2].predicted_ns=kSecond;
  growth[2].known=false;assert(!estimate().known);growth[2].known=true;
  blocks[2].flags=3;assert(!estimate().known);blocks[2].flags=7;
  blocks[2].flags=15;assert(!estimate().known);blocks[2].flags=7;
  history[2].known=false;assert(!estimate().known);history[2].known=true;
  history[2].interval10=1;assert(!estimate().known);history[2].interval10=10*kSecond;
  blocks[2].zone=9;assert(!estimate().known);blocks[2].zone=2;
  assert(!Estimate(cs[1],blocks[2],growth[2],history[2],0,H).known);
  assert(!Estimate(cs[1],blocks[2],growth[2],history[2],4096,H+1).known);
  // 没有资格、超预算、未知Greedy或Greedy已紧迫时，不扩大候选集合。
  cs[1].covered=false;cs[2].covered=false;assert(!evaluate().decision.would_switch);
  cs[1].covered=true;cs[2].covered=true;
  cs[1].valid=cs[2].valid=33*mib;assert(!evaluate().decision.would_switch);
  cs[1].valid=cs[2].valid=24*mib;
  growth[1].known=false;assert(!evaluate().decision.would_switch);growth[1].known=true;
  blocks[1].rc=4096;assert(!evaluate().decision.would_switch);blocks[1].rc=100;
  // 默认及观察模式原决策逐字段不变，只有显式执行使用新建议。
  rrzc_v1::ShadowDecision old;old.proposed_zone=99;old.reason="旧策略";
  for (auto mode:{Mode::Off,Mode::Observe}) {
    auto d=Apply(mode,old,first);assert(d.proposed_zone==99 && d.reason==old.reason);
  }
  assert(Apply(Mode::Execute,old,first).proposed_zone==2);
  assert(Parse(nullptr,true,true,false,false,false)==Mode::Off);
  assert(Parse("execute",true,true,false,false,false)==Mode::Execute);
  assert(Parse("observe",true,false,false,false,false)==Mode::Observe);
  for (auto request:{"1","invalid",""}) assert(Parse(request,true,true,false,false,false)==Mode::Off);
  assert(Parse("execute",false,true,false,false,false)==Mode::Off);
  assert(Parse("execute",true,false,false,false,false)==Mode::Off);
  assert(Parse("execute",true,true,true,false,false)==Mode::Off);
  assert(Parse("execute",true,true,false,true,false)==Mode::Off);
  assert(Parse("execute",true,true,false,false,true)==Mode::Off);
  // 整数乘积边界：最大合法字节量不溢出，源容量必须有正回收量。
  auto large=cs[1];large.valid=UINT64_MAX-1;large.capacity=UINT64_MAX;
  auto f=history[2];f.retained=large.valid;f.loss10=f.loss30=UINT64_MAX;
  auto cost=Estimate(large,blocks[2],growth[2],f,4096,H);
  assert(cost.known && cost.credit<=cost.retained && cost.retained<=large.valid);
  std::cout<<"V2净成本：时间、退役、空间单调性及默认关闭、资格预算、未知回退和整数边界通过\n";
}
