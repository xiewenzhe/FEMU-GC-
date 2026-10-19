// 合成容量边界，不替代真实设备令牌及管理锁验证。
#include "rrzc_reserve_exchange.h"
#include <cassert>
#include <iostream>
int main() {
  using namespace rrzc_exchange;
  State old{0,8,256,240,248},next{256,200,256,40,312};
  assert(CanExchange(64,old,next));
  assert(!CanExchange(8,old,next));
  assert(!CanExchange(201,old,next));
  assert(!CanExchange(0,old,next));
  auto bad=next;bad.zone=old.zone;assert(!CanExchange(64,old,bad));
  bad=next;bad.valid=0;assert(!CanExchange(64,old,bad));
  bad=next;bad.wp++;assert(!CanExchange(64,old,bad));
  bad=old;bad.remaining=0;assert(!CanExchange(64,bad,next));
  bad=old;bad.remaining=256;assert(!CanExchange(64,bad,next));
  assert(!Enabled(nullptr,true));assert(!Enabled("1",false));assert(Enabled("1",true));
  std::cout<<"预留归属交换边界检查通过\n";
}
