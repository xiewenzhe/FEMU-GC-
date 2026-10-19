// 合成有效量和片段边界只验证成本观察，不证明文件未来寿命或维护收益。
#include "../model/rrzc_cost_observe.h"
#include <cassert>
#include <iostream>

static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
int main() {
  using namespace rrzc_cost;
  const uint64_t mib=1ULL<<20;
  CHECK(!Enabled(nullptr,true)); CHECK(!Enabled("0",true)); CHECK(!Enabled("01",true));
  CHECK(!Enabled("1",false)); CHECK(Enabled("1",true));
  ValidityHistory history;
  auto frame=[&](uint64_t seconds,uint64_t valid) {
    ValidityFrame f; f.session_hi=1;
    ValidityPoint p; p.zone=1; p.time_ns=seconds*kSecond; p.valid=valid;
    p.known=true; f.zones.push_back(p); return f;
  };
  for (uint64_t t=1;t<=39;++t) CHECK(history.Push(frame(t,256*mib)));
  auto now=frame(40,32*mib);
  auto change=history.Check(now,now.zones[0],10*kSecond);
  CHECK(change.known && change.previous.time_ns==30*kSecond);
  CHECK(change.interval_ns==10*kSecond && change.decreased==224*mib && !change.increased);
  change=history.Check(now,now.zones[0],30*kSecond);
  CHECK(change.known && change.previous.time_ns==10*kSecond && change.decreased==224*mib);
  now.zones[0].valid=300*mib;
  change=history.Check(now,now.zones[0],10*kSecond);
  CHECK(change.known && change.increased==44*mib && !change.decreased);
  now.zones[0].valid=256*mib;
  change=history.Check(now,now.zones[0],10*kSecond);
  CHECK(change.known && !change.increased && !change.decreased);
  now.zones[0].known=false; CHECK(!history.Check(now,now.zones[0],10*kSecond).known);
  now=frame(40,32*mib);
  for (uint64_t ValidityPoint::* field:{&ValidityPoint::device_generation,&ValidityPoint::host_generation,
                                      &ValidityPoint::host_sequence}) {
    now.zones[0].*field=1; CHECK(!history.Check(now,now.zones[0],10*kSecond).known);
    now.zones[0].*field=0;
  }
  now.session_hi=2; CHECK(!history.Check(now,now.zones[0],10*kSecond).known);
  now=frame(40,32*mib);
  CHECK(!history.Check(now,now.zones[0],9*kSecond).known);
  CHECK(!history.Check(now,now.zones[0],0).known);
  auto unknown=frame(39,256*mib); unknown.zones[0].known=false;
  history.Clear(); CHECK(history.Push(frame(29,256*mib))); CHECK(history.Push(unknown));
  CHECK(!history.Check(now,now.zones[0],10*kSecond).known);
  history.Clear(); CHECK(history.Push(frame(27,256*mib)));
  CHECK(!history.Check(now,now.zones[0],10*kSecond).known);
  history.Clear(); CHECK(history.Push(frame(29,256*mib)));
  change=history.Check(now,now.zones[0],10*kSecond);
  CHECK(change.known && change.interval_ns==11*kSecond);
  history.Clear(); CHECK(history.Push(frame(31,256*mib)));
  CHECK(!history.Check(now,now.zones[0],10*kSecond).known);
  history.Clear(); CHECK(history.Push(frame(30,256*mib))); CHECK(history.Push(frame(40,256*mib)));
  CHECK(!history.Check(now,now.zones[0],10*kSecond).known);
  // 会话切换清历史；重复时间和重复区拒绝并清历史。
  history.Clear(); CHECK(history.Push(frame(1,256*mib)));
  auto other=frame(2,32*mib); other.session_hi=2; CHECK(history.Push(other));
  now=frame(40,32*mib); now.session_hi=2;
  CHECK(!history.Check(now,now.zones[0],30*kSecond).known);
  CHECK(!history.Push(other)); CHECK(!history.Check(now,now.zones[0],30*kSecond).known);
  other=frame(3,32*mib); other.zones.push_back(other.zones[0]); CHECK(!history.Push(other));
  CHECK(!history.Push({}));
  history.Clear();
  for (uint64_t t=1;t<=80;++t) CHECK(history.Push(frame(t,16*mib)));
  now=frame(81,16*mib);
  CHECK(history.Check(now,now.zones[0],30*kSecond).known);
  auto bounds=BoundPhysical(32*mib,4096,8);
  CHECK(bounds.known && bounds.lower==32*mib && bounds.upper==32*mib+4096);
  bounds=BoundPhysical(4095,4096,8);
  CHECK(bounds.known && bounds.lower==4096 && bounds.upper==8192);
  bounds=BoundPhysical(1,4096,8); CHECK(bounds.known && bounds.lower==4096 && bounds.upper==4096);
  CHECK(!BoundPhysical(0,4096,8).known); CHECK(!BoundPhysical(1,0,8).known);
  CHECK(!BoundPhysical(UINT64_MAX,4096,8).known);
  CHECK(!BoundPhysical(UINT32_MAX,4096,8).known);
  CHECK(BoundPhysical(4096,4096,0).upper==4096);
  std::cout<<"成本观察核心："<<checks<<"项检查通过（合成数据）\n";
}
