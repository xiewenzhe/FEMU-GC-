// 仅验证策略边界；这些合成数据不是设备实验或收益数字。
#include "rrzc_pair_admission.h"
#include <cassert>
#include <thread>
using namespace rrzc_pair;
static rrzc_v1::RecentBlock B(uint32_t id,uint64_t rc,uint64_t d10) {
  rrzc_v1::RecentBlock b;b.zone=7;b.generation=2;b.block_id=id;b.snapshot_ns=40*rrzc_v1::kSecond;
  b.rc=rc;b.delta10=d10;b.delta30=d10;
  b.flags=rrzc_v1::kEnabled|rrzc_v1::kValid10|rrzc_v1::kValid30;return b;
}
int main() {
  Ledger ledger;int a=0,b=0;
  ledger.Publish(&a,1,{{7,10}});ledger.Publish(&b,2,{{7,20}});
  auto live=ledger.Read(7);assert(live.total==30 && live.h==10 && live.w==20);
  ledger.Publish(&a,3,{{7,10}});live=ledger.Read(7);
  assert(live.total==30 && live.h==10 && live.w==30 && live.both==10);
  const auto revision=live.revision;ledger.Publish(&b,2,{});
  assert(!ledger.Unchanged(revision) && ledger.Read(7).w==10);
  ledger.Publish(&a,1,{{9,10}});assert(ledger.Read(7).total==0 && ledger.Read(9).h==10);
  std::thread t([&]{for(int n=0;n<1000;++n)ledger.Publish(&b,2,{{9,20}});});
  for(int n=0;n<1000;++n){auto row=ledger.Read(9);assert(row.total==10 || row.total==30);}
  t.join();ledger.Publish(&a,0,{});ledger.Publish(&b,0,{});assert(ledger.Read(9).total==0);
  // 最大RC块并不是最早到阈值者；冷块完整零窗口不能令全帧失效。
  auto risk=Forecast({B(0,900,1),B(1,500,400),B(2,0,0)},1024);
  assert(risk.known && risk.block==1 && risk.eta>=13100000000ULL && risk.eta<=13100000001ULL);
  auto bad=B(2,0,0);bad.flags=rrzc_v1::kEnabled;
  assert(!Forecast({B(0,900,1),bad},1024).known);
  bad=B(2,0,0);bad.generation=3;assert(!Forecast({B(0,900,1),bad},1024).known);
  bad=B(2,0,0);bad.flags|=rrzc_v1::kLoss;assert(!Forecast({bad},1024).known);
  auto faster=Forecast({B(0,900,1),B(1,500,400)},1024,100.0L/rrzc_v1::kSecond);
  assert(faster.known && faster.eta<risk.eta);
  Input i;i.live={30,10,20,0,0};i.native_valid=30;i.remaining=100;i.required=1;
  i.incoming=2;i.new_file=true;i.risk={true,5*rrzc_v1::kSecond,0,900};
  auto no=Evaluate(i);assert(!no.allow && no.companion_upper==120);
  i.budget=120;assert(Evaluate(i).allow);
  i.budget=0;i.risk.eta=11*rrzc_v1::kSecond;assert(Evaluate(i).allow);
  i.risk.known=false;assert(!Evaluate(i).allow);
  i.risk.known=true;i.incoming=1;assert(!Evaluate(i).allow);
  i.incoming_h_known=true;assert(Evaluate(i).allow);
  i.native_valid=31;assert(!Evaluate(i).allow);
  i.native_valid=30;i.live={30,0,30,0,0};i.incoming=2;i.risk={};
  assert(Evaluate(i).allow && !Evaluate(i).mixed);
  i.incoming=1;assert(!Evaluate(i).allow);
  // 必要W迁移已有精确申请量，不套用新文件的整段尾部上界。
  i.live={30,10,20,0,0};i.new_file=false;i.incoming=2;i.required=8;
  assert(Evaluate(i).companion_upper==28);
}
