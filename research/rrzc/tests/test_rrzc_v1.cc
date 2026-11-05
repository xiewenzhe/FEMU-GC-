// 合成数据仅用于协议和影子选择正确性测试，不是设备实验结果。
#include "../model/rrzc_urgency_selector.h"
#include <cassert>
#include <iostream>
#include <limits>

static unsigned checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
static void Put(std::vector<uint8_t>& v, size_t pos, uint64_t n, size_t bytes) {
  for (size_t i=0; i<bytes; ++i) v.at(pos+i)=uint8_t(n>>(8*i));
}
static std::vector<uint8_t> Page() {
  using namespace rrzc_v1;
  std::vector<uint8_t> v(kHeaderBytes+2*kEntryBytes);
  Put(v,0,1,4); Put(v,4,kEntryBytes,4); Put(v,8,2,4); Put(v,12,64,4);
  Put(v,16,4096,8); Put(v,24,42,8);
  Put(v,40,10*kSecond,8); Put(v,48,30*kSecond,8);
  for (size_t i=0; i<2; ++i) {
    size_t p=kHeaderBytes+i*kEntryBytes;
    Put(v,p,i*65536,8); Put(v,p+8,3+i,8);
    Put(v,p+16,100*kSecond,8); Put(v,p+24,50*kSecond,8);
    Put(v,p+32,2952,8); Put(v,p+40,100,8); Put(v,p+48,300,8);
    Put(v,p+56,i,4); Put(v,p+60,7+i,4); Put(v,p+68,3,4);
    Put(v,p+72,1,4); Put(v,p+76,i,4); Put(v,p+80,7,4);
  }
  return v;
}
int main() {
  using namespace rrzc_v1;
  auto v=Page(); RecentReport report; std::string error;
  auto decode=[&](const std::vector<uint8_t>& bytes) {
    return DecodeRecent(bytes.data(),bytes.size(),2,256ULL<<20,&report,&error);
  };
  CHECK(decode(v)); CHECK(error.empty()); CHECK(report.threshold==4096);
  CHECK(report.zones[1].zone==(256ULL<<20));
  CHECK(report.zones[1].block_id==8 && report.zones[1].generation==4);
  CHECK(report.zones[1].delta10==100 && report.zones[1].delta30==300);
  // 身份改变后的增量直接来自新块，不从两次最大读数相减。
  auto changed=v; Put(changed,kHeaderBytes+60,9,4);
  Put(changed,kHeaderBytes+32,3000,8); Put(changed,kHeaderBytes+40,7,8);
  CHECK(decode(changed)); CHECK(report.zones[0].block_id==9);
  CHECK(report.zones[0].delta10==7);
  auto reset=v; Put(reset,kHeaderBytes+8,4,8);
  Put(reset,kHeaderBytes+24,100*kSecond,8);
  Put(reset,kHeaderBytes+32,0,8); Put(reset,kHeaderBytes+40,0,8);
  Put(reset,kHeaderBytes+48,0,8); Put(reset,kHeaderBytes+80,kEnabled,4);
  CHECK(decode(reset)); CHECK(report.zones[0].generation==4);
  CHECK(!TimeToThreshold(report.zones[0],4096).known);
  struct Bad { size_t pos; uint64_t value; size_t bytes; };
  const Bad bad[]={{0,2,4},{4,80,4},{8,3,4},{12,0,4},{16,0,8},
    {24,0,8},{40,9*kSecond,8},{48,31*kSecond,8},
    {kHeaderBytes,1,8},{kHeaderBytes+56,1,4},{kHeaderBytes+60,64,4},
    {kHeaderBytes+80,16,4},{kHeaderBytes+84,1,4},
    {kHeaderBytes+24,101*kSecond,8},{kHeaderBytes+40,301,8},
    {kHeaderBytes+48,3000,8},{kHeaderBytes+80,5,4},
    {kHeaderBytes+80,15,4},{kHeaderBytes+24,99*kSecond,8}};
  for (const auto& b:bad) {
    auto bytes=v; Put(bytes,b.pos,b.value,b.bytes);
    CHECK(!decode(bytes)); CHECK(!error.empty()); CHECK(report.zones.empty());
  }
  auto truncated=v; truncated.pop_back(); CHECK(!decode(truncated));
  auto longer=v; longer.push_back(0); CHECK(!decode(longer));
  CHECK(!DecodeRecent(nullptr,v.size(),2,256ULL<<20,&report,&error));
  CHECK(!DecodeRecent(v.data(),v.size(),2,0,&report,&error));
  CHECK(decode(v));
  RecentBlock cold=report.zones[0],hot=cold;
  cold.rc=100; cold.delta10=cold.delta30=0;
  hot.rc=4000; hot.delta10=200; hot.delta30=600;
  CHECK(TimeToThreshold(hot,4096).threshold_ns==4800000000ULL);
  hot.rc=2952; hot.delta10=20; hot.delta30=60;
  CHECK(TimeToThreshold(hot,4096).threshold_ns==572*kSecond);
  CHECK(TimeToThreshold(cold,4096).threshold_ns==UINT64_MAX);
  CHECK(!TimeToThreshold(cold,0).known);
  hot.rc=4000; hot.delta10=200; hot.delta30=30;
  CHECK(TimeToThreshold(hot,4096).threshold_ns==96*kSecond);
  hot.flags=kEnabled; CHECK(!TimeToThreshold(hot,4096).known);
  hot.rc=4096; CHECK(TimeToThreshold(hot,4096).threshold_ns==0);
  hot.flags=0; CHECK(!TimeToThreshold(hot,4096).known);
  hot=cold; hot.flags=7; hot.rc=4000; hot.delta10=200; hot.delta30=600;
  constexpr uint64_t mib=1ULL<<20;
  std::vector<rrzc_simple::Candidate> candidates={
      {1,16*mib,256*mib,0,0,true,true,false},
      {2,32*mib,256*mib,0,0,true,true,false},
      {3,48*mib,256*mib,0,0,true,true,false}};
  std::map<uint64_t,RecentBlock> recent={{1,cold},{2,hot},{3,hot}};
  auto evaluate=[&]() { return Evaluate(candidates,recent,4096,192*mib,10*kSecond,16*mib); };
  auto d=evaluate(); CHECK(d.greedy.selected.zone==1);
  CHECK(d.would_switch && d.proposed_zone==2 && d.extra_valid==16*mib);
  recent[2].rc=2952; recent[2].delta10=20; recent[2].delta30=60;
  d=evaluate(); CHECK(!d.would_switch && d.proposed_zone==1);
  CHECK(std::string(d.reason)=="紧迫候选超出额外有效搬运预算");
  recent[3]=cold; d=evaluate(); CHECK(!d.would_switch);
  recent[2]=hot; recent[1]=hot;
  d=evaluate(); CHECK(!d.would_switch && d.proposed_zone==1);
  CHECK(std::string(d.reason)=="Greedy自身已经紧迫");
  recent[1]=cold; recent[1].flags=kEnabled;
  CHECK(!evaluate().would_switch);
  recent.erase(1); CHECK(!evaluate().would_switch);
  recent[1]=cold; recent[2].flags=kEnabled; CHECK(!evaluate().would_switch);
  recent[2]=hot; candidates[1].target_available=false; CHECK(!evaluate().would_switch);
  candidates[1].target_available=true; candidates[1].covered=false;
  CHECK(!evaluate().would_switch); candidates[1].covered=true;
  CHECK(!Evaluate(candidates,recent,4096,192*mib,0,16*mib).would_switch);
  CHECK(!Evaluate(candidates,recent,4096,192*mib,301*kSecond,16*mib).would_switch);
  CHECK(!Evaluate(candidates,recent,4096,192*mib,10*kSecond,15*mib).would_switch);
  // 当前零回收预算表示取消额外固定门槛，不再表示禁用候选。
  CHECK(Evaluate(candidates,recent,4096,0,10*kSecond,16*mib).greedy.found);
  CHECK(!Evaluate({},recent,4096,192*mib,10*kSecond,16*mib).greedy.found);
  candidates[2].valid=32*mib; recent[3]=hot;
  CHECK(evaluate().proposed_zone==2);
  recent[3].rc=4050; CHECK(evaluate().proposed_zone==3);
  // 大数计算不溢出；向上取整，不因整数截断误报更早到阈值。
  hot.rc=0; hot.delta10=hot.delta30=1;
  CHECK(TimeToThreshold(hot,UINT64_MAX).threshold_ns==UINT64_MAX);
  hot.rc=4095; hot.delta10=3; hot.delta30=9;
  CHECK(TimeToThreshold(hot,4096).threshold_ns==3333333334ULL);
  hot.delta10=UINT64_MAX; hot.delta30=UINT64_MAX;
  CHECK(TimeToThreshold(hot,4096).threshold_ns==1);
  std::cout<<"C2h协议和V1影子选择："<<checks<<"项检查通过（合成数据）\n";
}
