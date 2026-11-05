// 合成边界检查，不是设备收益或RR实际节省量。
#include "rrzc_first_action.h"
#include <cassert>
using namespace rrzc_first_action;
static rrzc_all_blocks::Report Frame(uint64_t time,uint64_t first,uint64_t second) {
  rrzc_all_blocks::Report r;r.threshold=1024;r.session_hi=5;r.nr_zones=64;r.blocks_per_zone=2;
  for(unsigned id=0;id<2;++id) {
    rrzc_v1::RecentBlock b;b.zone=128ULL<<20;b.zone_index=1;b.block_id=id;b.ch=id;
    b.snapshot_ns=time*rrzc_v1::kSecond;b.rc=id?second:first;
    b.delta10=id?400:10;b.delta30=id?400:980;b.flags=7;r.blocks.push_back(b);
  }
  return r;
}
int main() {
  Files files;files.Observe(1,{{1,1024,true}});files.Observe(5,{{1,1024,true}});
  assert(files.Span(1,1024,5)==4);
  files.Observe(6,{});files.Observe(7,{{1,1024,true}});assert(!files.Span(1,1024,7));
  files.Observe(8,{{1,512,true}});assert(!files.Span(1,512,8));
  files.Observe(3,{{1,512,true}});assert(!files.Span(1,512,3));
  assert(Credit(100,rrzc_v1::kSecond,0)==0);
  assert(Credit(100,kScaleCap,UINT64_MAX)==0);
  assert(Credit(100,kScaleCap,kScaleCap)==50);
  assert(Credit(UINT64_MAX,kScaleCap,UINT64_MAX-1)<=UINT64_MAX);
  rrzc_simple::Candidate c{128ULL<<20,11ULL<<20,128ULL<<20,980,1024,true,true,true};
  rrzc_all_blocks::History h;h.Push(Frame(37,977,380));h.Push(Frame(38,978,420));h.Push(Frame(39,979,460));
  auto current=Frame(40,980,500);const auto risk=Predict(c,&current,&h);
  assert(risk.known && risk.block==1 && risk.eta==13100000000ULL);
  // 较低RC但更快的块先到阈值；预测可超过历史窗口且不清零。
  auto reset=current;for(auto& b:reset.blocks)++b.generation;
  assert(!Predict(c,&reset,&h).known);
  rrzc_simple::Candidate cold=c;cold.zone=2*(128ULL<<20);cold.valid=8ULL<<20;
  assert(Select({{cold,true,{true,UINT64_MAX,0},0},{c,true,risk,Credit(8ULL<<20,kScaleCap,risk.eta)}}).zone==c.zone);
  // 风险不够抵偿成本时保留空间收益更高者；未知不抵扣，平分按稳定编号。
  assert(Select({{cold,true,{true,UINT64_MAX,0},0},{c,true,risk,0}}).zone==cold.zone);
  assert(Select({{cold,true,{true,UINT64_MAX,0},0},{c,true,{},c.valid}}).zone==cold.zone);
  assert(!Select({{c,false,risk,0}}).found);
}
