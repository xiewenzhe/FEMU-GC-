// 独立验证计划总量、成本轮换和最终业务清单大小。
#include "model_workload_plan.h"
#include <cassert>
#include <iostream>
int main() {
  unsigned expected[]={16,24,32,16,24,32};
  for(unsigned g=0;g<6;++g) {
    assert(rrzc_workload::AnchorMiB(g,0)==16);
    assert(rrzc_workload::AnchorMiB(g,1)==expected[g]);
    unsigned total=0;
    for(unsigned k=0;k<2;++k) {
      total+=rrzc_workload::AnchorMiB(g,k)+rrzc_workload::VersionMiB(g,k);
      assert(rrzc_workload::AnchorMiB(g,k)*(1U<<20)>7*65536+4096);
    }
    assert(total==512);
  }
  assert(rrzc_workload::FinalBytes(180)==632ULL*(1<<20));
  std::cout<<"成本计划验证通过：每代512MiB，三档热锚点，最终632MiB。\n";
}
