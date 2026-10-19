// 合成周转边界；物理令牌、尾部和数据正确性另做设备验证。
#include "rrzc_empty_reserve.h"
#include <cassert>
#include <iostream>
int main() {
  using rrzc_empty::Plan;
  assert(!Plan(true,true,false,false).rotate);
  assert(!Plan(false,true,true,false).rotate);
  auto p=Plan(false,true,false,false);assert(p.rotate && p.need_token);
  p=Plan(false,false,false,false);assert(p.rotate && !p.need_token);
  p=Plan(false,true,false,true);assert(p.rotate && !p.need_token);
  assert(!rrzc_empty::Enabled(nullptr,true));
  assert(!rrzc_empty::Enabled("1",false));
  assert(rrzc_empty::Enabled("1",true));
  std::cout<<"完整空预留周转边界检查通过\n";
}
