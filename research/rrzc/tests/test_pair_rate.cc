// 仅合成数据检查，不是设备实验或性能结果。
#include "rrzc_pair_rate.h"
#include <cassert>
#include <iostream>

int main() {
  using namespace rrzc_v1;
  unsigned checks=0;
  auto expect=[&](bool b){assert(b);++checks;};
  expect(!PairRateEnabled(nullptr,true,true,false));
  expect(!PairRateEnabled("0",true,true,false));
  expect(!PairRateEnabled("1",false,true,false));
  expect(!PairRateEnabled("1",true,false,false));
  expect(!PairRateEnabled("1",true,true,true));
  expect(PairRateEnabled("1",true,true,false));
  RecentReport a; a.threshold=4096; a.blocks_per_zone=64; a.session_hi=1;
  RecentBlock p; p.zone=256; p.flags=kEnabled; p.snapshot_ns=10*kSecond; p.rc=3000;
  a.zones={p};auto b=a;b.zones[0].snapshot_ns+=kSecond;b.zones[0].rc+=100;
  auto current=b;current.zones[0].snapshot_ns+=kSecond/4;current.zones[0].rc+=25;
  PairRateHistory history;history.Publish(a,true);history.Publish(b,true);
  auto frozen=history.Freeze();
  auto check=[&](const PairFrames& f,const RecentReport& c,bool usable=true) {
    return CheckPairRate(f,c,c.zones[0],usable,10*kSecond);
  };
  auto e=check(frozen,current);
  expect(e.known&&e.delta==100&&e.interval_ns==kSecond&&e.predicted_ns==9710000000ULL&&e.urgent);
  GrowthHistory legacy;legacy.Push(b);
  expect(!legacy.Check(current,current.zones[0],10*kSecond).known);
  // 改变当前查询相位，但不借当前增量估速；仍使用固定的最近两端点。
  for (auto gap:{uint64_t{1},kSecond/4,kSecond/2,kSecond}) {
    auto c=current;c.zones[0].snapshot_ns=b.zones[0].snapshot_ns+gap;
    expect(check(frozen,c).known&&check(frozen,c).delta==100);
  }
  auto c=current;c.zones[0].snapshot_ns=b.zones[0].snapshot_ns+kSecond+1;
  expect(!check(frozen,c).known);
  expect(!check(frozen,current,false).known);
  for (unsigned kind=0;kind<13;++kind) {
    auto f=frozen;
    auto& r=f.reports.front();auto& q=r.zones[0];
    switch(kind) {
      case 0:++r.session_hi;break;case 1:++r.threshold;break;
      case 2:++r.blocks_per_zone;break;case 3:++q.generation;break;
      case 4:++q.epoch_start_ns;break;case 5:++q.block_id;break;
      case 6:++q.ch;break;case 7:++q.lun;break;case 8:++q.plane;break;
      case 9:++q.block;break;case 10:++q.zone_index;break;
      case 11:q.flags=0;break;case 12:r.zones.clear();break;
    }
    expect(!check(f,current).known);
  }
  c=current;++c.zones[0].block_id;expect(!check(frozen,c).known);
  c=current;++c.zones[0].generation;expect(!check(frozen,c).known);
  for (unsigned kind=0;kind<4;++kind) {
    auto f=frozen;
    if(kind==0) f.reports.front().zones[0].rc=3200;
    if(kind==1) f.reports.back().zones[0].rc=3200;
    if(kind==2) f.reports.front().zones[0].snapshot_ns=f.reports.back().zones[0].snapshot_ns;
    if(kind==3) f.reports.back().zones[0].snapshot_ns=current.zones[0].snapshot_ns;
    expect(!check(f,current).known);
  }
  auto f=frozen;f.reports.front().zones[0].snapshot_ns=b.zones[0].snapshot_ns-kSecond/2;
  expect(check(f,current).known);
  ++f.reports.front().zones[0].snapshot_ns;expect(!check(f,current).known);
  f=frozen;f.reports.front().zones[0].rc=f.reports.back().zones[0].rc;
  expect(check(f,current).known&&!check(f,current).urgent&&check(f,current).predicted_ns==UINT64_MAX);
  c=current;c.zones[0].rc=4096;
  expect(check(f,c).known&&check(f,c).urgent&&check(f,c).predicted_ns==0);
  // 溢出采用饱和值，不得到看似紧迫的小数。
  c=current;c.threshold=UINT64_MAX;f=frozen;
  for(auto& r:f.reports) r.threshold=UINT64_MAX;
  f.reports.back().zones[0].rc=f.reports.front().zones[0].rc+1;
  expect(check(f,c).known&&check(f,c).predicted_ns==UINT64_MAX);
  history.Publish(current,true);expect(frozen.reports.front().zones[0].rc==3000);
  history.Publish({},false);expect(history.Freeze().reports.empty());
  history.Publish(a,true);auto session=b;++session.session_hi;history.Publish(session,true);
  expect(history.Freeze().reports.size()==1);
  auto adapted=PairAsShadowEvidence(e);
  expect(ShortTimeToThreshold(current.zones[0],adapted,current.threshold).threshold_ns==e.predicted_ns);
  std::cout<<"双点估速："<<checks<<"项合成检查通过；原增长入口不变。\n";
}
