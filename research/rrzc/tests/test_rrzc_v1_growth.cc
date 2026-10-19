// 合成同块快照只验证准入与保守回退，不是实际SSD收益或性能数据。
#include "../model/rrzc_v1_growth.h"
#include <cassert>
#include <iostream>

static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
int main() {
  using namespace rrzc_v1;
  CHECK(!GrowthEnabled(nullptr,true));
  CHECK(!GrowthEnabled("0",true));
  CHECK(!GrowthEnabled("01",true));
  CHECK(!GrowthEnabled("1",false));
  CHECK(GrowthEnabled("1",true));
  RecentBlock hot;
  hot.zone=2; hot.zone_index=2; hot.rc=4040; hot.delta10=hot.delta30=4040;
  hot.snapshot_ns=40*kSecond; hot.flags=kEnabled|kValid10|kValid30;
  RecentReport current; current.session_hi=1; current.threshold=4096;
  current.blocks_per_zone=64; current.zones={hot};
  RecentReport before=current;
  before.zones[0].snapshot_ns-=kSecond; before.zones[0].rc-=16;
  GrowthHistory history;
  CHECK(!history.Check(current,hot,10*kSecond).known);
  history.Push(before);
  auto gate=history.Check(current,hot,10*kSecond);
  CHECK(gate.known && gate.pass && gate.delta==16 && gate.interval_ns==kSecond);
  CHECK(gate.predicted_ns==3500000000ULL);
  auto stopped=before; stopped.zones[0].rc=hot.rc;
  history.Clear(); history.Push(stopped);
  gate=history.Check(current,hot,10*kSecond);
  CHECK(gate.known && !gate.pass && gate.delta==0);
  stopped.zones[0].rc=hot.rc-4;
  history.Clear(); history.Push(stopped);
  gate=history.Check(current,hot,10*kSecond);
  CHECK(gate.known && !gate.pass && gate.predicted_ns==14*kSecond);
  auto check_unknown=[&](RecentReport previous) {
    history.Clear(); history.Push(previous);
    CHECK(!history.Check(current,hot,10*kSecond).known);
  };
  auto changed=before; changed.session_hi++; check_unknown(changed);
  changed=before; changed.session_lo++; check_unknown(changed);
  changed=before; changed.threshold++; check_unknown(changed);
  changed=before; changed.blocks_per_zone++; check_unknown(changed);
  changed=before; changed.zones[0].generation++; check_unknown(changed);
  changed=before; changed.zones[0].epoch_start_ns++; check_unknown(changed);
  changed=before; changed.zones[0].block_id++; check_unknown(changed);
  changed=before; changed.zones[0].zone_index++; check_unknown(changed);
  changed=before; changed.zones[0].ch++; check_unknown(changed);
  changed=before; changed.zones[0].lun++; check_unknown(changed);
  changed=before; changed.zones[0].plane++; check_unknown(changed);
  changed=before; changed.zones[0].block++; check_unknown(changed);
  changed=before; changed.zones[0].flags=0; check_unknown(changed);
  changed=before; changed.zones[0].rc=hot.rc+1; check_unknown(changed);
  changed=before; changed.zones[0].snapshot_ns=hot.snapshot_ns; check_unknown(changed);
  changed=before; changed.zones[0].snapshot_ns=hot.snapshot_ns+1; check_unknown(changed);
  changed=before; changed.zones[0].snapshot_ns=hot.snapshot_ns-kGrowthMaxNs-1; check_unknown(changed);
  changed=before; changed.zones[0].snapshot_ns=hot.snapshot_ns-kGrowthMinNs+1; check_unknown(changed);
  changed=before; changed.zones.clear(); check_unknown(changed);
  // 最新点略不足一秒仍可使用，但不能借用更旧的突发点。
  auto close=before; close.zones[0].snapshot_ns=hot.snapshot_ns-kSecond/2;
  close.zones[0].rc=hot.rc-8;
  history.Clear(); history.Push(before); history.Push(close);
  CHECK(history.Check(current,hot,10*kSecond).pass);
  changed=close; changed.zones[0].rc=hot.rc;
  history.Clear(); history.Push(before); history.Push(changed);
  gate=history.Check(current,hot,10*kSecond);
  CHECK(gate.known && !gate.pass && gate.delta==0);
  changed.zones[0].snapshot_ns=hot.snapshot_ns-kGrowthMinNs+1;
  history.Clear(); history.Push(before); history.Push(changed);
  CHECK(!history.Check(current,hot,10*kSecond).known);
  gate=history.Check(current,hot,10*kSecond,true);
  CHECK(gate.known && gate.delta==16 && gate.interval_ns==kSecond);
  // 对齐仍不能跨越最近点的身份变化、Reset或读数回退。
  for (unsigned failure=0;failure<4;++failure) {
    auto invalid=changed;
    if (failure==0) ++invalid.zones[0].block_id;
    if (failure==1) ++invalid.zones[0].generation;
    if (failure==2) ++invalid.zones[0].epoch_start_ns;
    if (failure==3) invalid.zones[0].rc=hot.rc+1;
    history.Clear();history.Push(before);history.Push(invalid);
    CHECK(!history.Check(current,hot,10*kSecond,true).known);
  }
  // 已满足半秒窗口的零增量必须保留，不借用更早突发读制造紧迫性。
  changed.zones[0].snapshot_ns=hot.snapshot_ns-kGrowthMinNs;
  history.Clear();history.Push(before);history.Push(changed);
  gate=history.Check(current,hot,10*kSecond,true);
  CHECK(gate.known && !gate.pass && gate.delta==0);
  changed=close; changed.zones[0].block_id++;
  history.Clear(); history.Push(before); history.Push(changed);
  CHECK(!history.Check(current,hot,10*kSecond).known);
  changed=close; changed.zones[0].rc=hot.rc+1;
  history.Clear(); history.Push(before); history.Push(changed);
  CHECK(!history.Check(current,hot,10*kSecond).known);
  history.Clear(); history.Push(before);
  CHECK(!history.Check(current,hot,0).known);
  CHECK(!history.Check(current,hot,301*kSecond).known);
  changed=current; changed.threshold=0;
  CHECK(!history.Check(changed,hot,10*kSecond).known);
  changed=current; changed.zones[0].rc=4096;
  CHECK(history.Check(changed,changed.zones[0],10*kSecond).predicted_ns==0);
  // 多候选中不能仅拒绝原第一名后就停，仍须考虑其他合格且持续读取的区。
  constexpr uint64_t mib=1ULL<<20;
  std::vector<rrzc_simple::Candidate> candidates={
      {1,16*mib,256*mib,0,0,true,true,false},
      {2,32*mib,256*mib,0,0,true,true,false},
      {3,24*mib,256*mib,0,0,true,true,false}};
  auto cold=hot; cold.zone=1; cold.zone_index=1; cold.rc=0; cold.delta10=cold.delta30=0;
  auto second=hot; second.zone=3; second.zone_index=3; second.rc=4000;
  second.delta10=second.delta30=3000;
  current.zones={cold,hot,second};
  before=current;
  for (auto& b:before.zones) b.snapshot_ns-=kSecond;
  before.zones[2].rc-=16; // 原最紧迫区停止，次紧迫区仍读。
  history.Clear(); history.Push(before);
  std::map<uint64_t,RecentBlock> usable={{1,cold},{2,hot},{3,second}};
  auto result=EvaluateWithGrowth(candidates,usable,current,history,192*mib,10*kSecond,16*mib);
  CHECK(result.original.would_switch && result.original.proposed_zone==2);
  CHECK(result.admitted.would_switch && result.admitted.proposed_zone==3);
  CHECK(result.admitted.extra_valid==8*mib && result.admitted.predicted_ns==960000000);
  CHECK(!result.evidence.at(2).pass && result.evidence.at(3).pass);
  result=EvaluateWithGrowth(candidates,usable,current,history,192*mib,10*kSecond,7*mib);
  CHECK(!result.admitted.would_switch && result.admitted.proposed_zone==1);
  usable.erase(3);
  result=EvaluateWithGrowth(candidates,usable,current,history,192*mib,10*kSecond,16*mib);
  CHECK(!result.admitted.would_switch && result.admitted.proposed_zone==1);
  CHECK(!result.evidence.at(3).known);
  usable.erase(1);
  result=EvaluateWithGrowth(candidates,usable,current,history,192*mib,10*kSecond,16*mib);
  CHECK(!result.admitted.would_switch && result.admitted.proposed_zone==1);
  history.Clear();
  CHECK(!history.Check(current,hot,10*kSecond).known);
  // 阈值重配/新会话必须清空旧帧，不能恢复原会话后再次借用。
  close.zones[0].snapshot_ns=hot.snapshot_ns-kGrowthMinNs+1;
  history.Push(before); changed=close; changed.session_hi++;
  history.Push(changed); history.Push(close);
  CHECK(!history.Check(current,hot,10*kSecond).known);
  // 四帧上界，不积累不受限历史。
  history.Clear(); history.Push(before);
  for (unsigned i=0;i<4;++i) {
    close=current;
    for (auto& b:close.zones) b.snapshot_ns=current.zones[0].snapshot_ns-(4-i)*100000000;
    history.Push(close);
  }
  CHECK(!history.Check(current,hot,10*kSecond).known);
  std::cout<<"V1同块增长准入："<<checks<<"项检查通过（合成数据）\n";
}
