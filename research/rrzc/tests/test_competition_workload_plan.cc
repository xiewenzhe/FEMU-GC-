// 独立检查三候选测试计划；不将预设值当成真实设备观测。
#include "competition_workload_plan.h"
#include <cassert>
#include <iostream>
int main() {
  unsigned sum=0;
  for(unsigned k=0;k<3;++k) {
    assert(rrzc_workload::AnchorMiB(0,k)==16+8*k);
    sum+=rrzc_workload::AnchorMiB(0,k)+rrzc_workload::VersionMiB(0,k);
  }
  assert(sum==768 && sum*rrzc_workload::PaceNs==30000000000ULL);
  assert(rrzc_workload::PostRate(1)==28 && rrzc_workload::PostRate(2)==32);
  assert(4*(16+24+32)+(240+232+224)==984);
  std::cout<<"三候选计划通过：每代768MiB，最终业务984MiB。\n";
}
