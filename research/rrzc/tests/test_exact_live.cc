// 边界及当前失败现场数值的回归，不是新的设备实验结果。
#include "rrzc_exact_live.h"
#include <cassert>
#include <iostream>
int main() {
  using rrzc_exact::Selected;
  constexpr uint64_t c=256ULL<<20;
  const uint64_t valid=2302557,garbage=100-100*valid/c;
  assert(garbage==100);
  assert(!Selected(valid,c,garbage,58,false));
  assert(Selected(valid,c,garbage,58,true));
  assert(!Selected(0,c,100,58,true));
  assert(Selected(1,c,100,58,true));
  assert(!Selected(113736371,c,58,58,true));
  assert(!Selected(c,c,0,58,true));
  assert(!Selected(1,0,100,58,true));
  assert(!rrzc_exact::Enabled(nullptr,true));
  assert(!rrzc_exact::Enabled("1",false));
  assert(rrzc_exact::Enabled("1",true));
  std::cout<<"真实有效字节判空边界检查通过\n";
}
