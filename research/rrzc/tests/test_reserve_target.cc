// 只验证保守资格边界，不将单元测试当作设备实验。
#include "rrzc_reserve_target.h"
#include <cassert>
#include <iostream>

int main() {
  using rrzc_target::Fits;
  rrzc_cost::ReserveObservation r;
  r.enabled=r.known=r.present=true;
  r.zone=4096; r.wp=8192; r.remaining=12288; r.valid=4096;
  rrzc_guard::Bounds b; b.Add(8192,4096,8,16384);
  assert(b.upper==12288);
  assert(Fits(32768,8192,true,b,16384,r,r));
  assert(!Fits(4096,8192,true,b,16384,r,r));
  assert(!Fits(32768,8192,false,b,16384,r,r));
  assert(!Fits(32768,8191,true,b,16384,r,r));
  auto x=r; --x.remaining;
  assert(!Fits(32768,8192,true,b,16384,r,x));
  ++x.wp;
  assert(!Fits(32768,8192,true,b,16384,x,x));
  x=r; ++x.host_generation;
  assert(!Fits(32768,8192,true,b,16384,r,x));
  x=r; ++x.host_sequence;
  assert(!Fits(32768,8192,true,b,16384,r,x));
  x=r; x.known=false;
  assert(!Fits(32768,8192,true,b,16384,x,x));
  x=r; x.present=false;
  assert(!Fits(32768,8192,true,b,16384,x,x));
  x=r; x.valid=16384;
  assert(!Fits(32768,8192,true,b,16384,x,x));
  x=r; x.wp=0;
  assert(!Fits(32768,8192,true,b,16384,x,x));
  rrzc_guard::Bounds overflow; overflow.Add(UINT64_MAX,4096,8,16384);
  assert(!Fits(32768,8192,true,overflow,16384,r,r));
  assert(!rrzc_target::Enabled(nullptr,true,true));
  assert(!rrzc_target::Enabled("1",false,true));
  assert(!rrzc_target::Enabled("1",true,false));
  assert(rrzc_target::Enabled("1",true,true));
  std::cout << "预留目标资格边界检查通过\n";
}
