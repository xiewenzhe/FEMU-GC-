// 固定负载安排的内存检查，不代表实际SSD事件。
#include "lifetime_workload_plan.h"
#include <cassert>
#include <iostream>
int main() {
  assert(rrzc_lifetime::Early(0,0) && !rrzc_lifetime::Early(0,1));
  assert(!rrzc_lifetime::Early(1,0) && !rrzc_lifetime::Early(1,2));
  assert(rrzc_lifetime::Early(2,1) && rrzc_lifetime::Early(2,2));
  assert(rrzc_lifetime::KeepRead(2,2,66999999999ULL));
  assert(!rrzc_lifetime::KeepRead(2,2,67000000000ULL));
  assert(rrzc_lifetime::KeepRead(1,2,119000000000ULL));
  assert(984-24-32-16==912);
  assert(rrzc_lifetime::ConfigureRetireDelay(nullptr) && rrzc_lifetime::RetireAge==68);
  assert(rrzc_lifetime::ConfigureRetireDelay("20") && rrzc_lifetime::RetireAge==88);
  assert(rrzc_lifetime::StopAge==67 && !rrzc_lifetime::KeepRead(0,0,67000000000ULL));
  assert(!rrzc_lifetime::ConfigureRetireDelay("1"));
  assert(!rrzc_lifetime::ConfigureRetireDelay("020"));
  assert(rrzc_lifetime::ConfigureRetireDelay("0") && rrzc_lifetime::RetireAge==68);
  std::cout<<"失效计划边界检查通过。\n";
}
