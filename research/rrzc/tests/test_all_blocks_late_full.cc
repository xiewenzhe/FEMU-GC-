// 合成回归：验证晚开始采样与真实零增长的区别，不代表实际C4帧或设备收益。
#include "rrzc_all_blocks.h"
#include <cassert>
#include <iostream>
using namespace rrzc_all_blocks;
static Report Sample(uint64_t t) {
  Report r;r.threshold=1024;r.nr_zones=64;r.blocks_per_zone=128;r.session_hi=1;
  for(unsigned id=0;id<128;++id) {
    RecentBlock b;b.zone=9*(128ULL<<20);b.zone_index=9;b.block_id=id;
    b.ch=id/16;b.lun=(id/4)%4;b.plane=id%4;b.block=9;b.generation=4;b.epoch_start_ns=1;
    b.snapshot_ns=t;b.rc=id==34?976:0;b.delta10=0;b.delta30=b.rc;b.flags=7;
    r.blocks.push_back(b);
  }
  return r;
}
int main() {
  auto now=Sample(40*kSecond);const auto& block=now.blocks[34];
  rrzc_simple::Candidate c;c.zone=block.zone;c.valid=21145689;c.capacity=128ULL<<20;
  History late,early;late.Push(Sample(39*kSecond));
  for(unsigned age=4;age;--age)early.Push(Sample((40-age)*kSecond));
  auto missing=late.Check(now,block,10*kSecond),idle=early.Check(now,block,10*kSecond);
  assert(!missing.known && idle.known && idle.delta==0 && idle.interval_ns==2*kSecond);
  Evidence a,b;
  const auto old_cost=Estimate(c,&now,&late,nullptr,10*kSecond,&a);
  const auto new_cost=Estimate(c,&now,&early,nullptr,10*kSecond,&b);
  assert(!old_cost.risk_known && !a.selected);
  assert(new_cost.risk_known && b.selected && new_cost.eta==UINT64_MAX && new_cost.credit==0);
  // 不通过预采样借用Reset前的历史，也不能把换PPA的计数当同块增长。
  auto reset=now;++reset.blocks[34].generation;
  assert(!early.Check(reset,reset.blocks[34],10*kSecond).known);
  auto moved=now;++moved.blocks[34].ch;
  assert(!early.Check(moved,moved.blocks[34],10*kSecond).known);
  std::cout<<"晚采样未知与完整零增长可区分；补齐历史不会凭空生成RR收益；Reset与PPA边界通过\n";
}
