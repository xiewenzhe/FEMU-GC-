// 仅测试计划：成本按固定代际轮换，不读取RC或维护结果，不输入生产选择器。
#pragma once
#include <cstdint>
namespace rrzc_workload {
constexpr unsigned AnchorMiB(unsigned generation,unsigned kind) {
  return kind ? 16+8*(generation%3) : 16;
}
constexpr unsigned VersionMiB(unsigned generation,unsigned kind) {
  return 256-AnchorMiB(generation,kind);
}
constexpr uint64_t FinalBytes(unsigned seconds) {
  const unsigned last=seconds/30-1;
  uint64_t mib=0;
  for(unsigned g=last>=3?last-3:0;g<=last;++g)
    for(unsigned kind=0;kind<2;++kind) mib+=AnchorMiB(g,kind);
  for(unsigned kind=0;kind<2;++kind) mib+=VersionMiB(last,kind);
  return mib*(1ULL<<20);
}
}  // 命名空间结束。
