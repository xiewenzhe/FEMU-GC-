// 以下均为合成调度测试，不作为真实RR或性能收益。
#include "rrzc_rr_fair_queue.h"
#include <algorithm>
#include <cassert>
#include <iostream>
#include <vector>
int main() {
  rrzc_rr_fair::Queue q;
  // 复现归档：3/24首次一起越阈；3被Reset后再热，24必须排在新3和26之前。
  q.Observe(3,true,0,0,true,65);q.Observe(24,true,0,0,true,65);
  assert(q.Before(3,24));
  q.Observe(3,true,2,4,true,75);q.Observe(24,true,0,0,true,75);
  q.Observe(26,true,2,4,true,75);
  std::vector<uint64_t> zones={3,24,26};
  std::sort(zones.begin(),zones.end(),[&](auto a,auto b){return q.Before(a,b);});
  assert((zones==std::vector<uint64_t>{24,3,26}) && q.First(24)==65);
  q.Observe(24,false,0,0,false,85);assert(q.First(24)==65);
  q.Observe(24,true,0,0,false,95);assert(q.First(24)==UINT64_MAX);
  q.Observe(24,true,0,0,true,105);assert(q.First(24)==105);
  q.Observe(24,true,1,2,true,115);assert(q.First(24)==115);
  q.Observe(24,true,1,4,true,125);assert(q.First(24)==125);
  // 新实例无历史；不同到达次序不能让低地址反复插队。
  rrzc_rr_fair::Queue other;assert(other.First(24)==UINT64_MAX);
  q.Observe(31,true,0,0,true,130);q.Observe(1,true,0,0,true,140);
  assert(q.Before(31,1));q.Observe(1,true,0,0,true,150);assert(q.First(1)==140);
  std::cout<<"RR等待优先、Reset/序号、未知观察和实例隔离边界通过\n";
}
