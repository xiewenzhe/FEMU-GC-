// 实验客户端的内部随机性控制；不消耗请求随机数，不改变原库或设备状态。
#pragma once
#include <cstdint>
#include "util/random.h"

namespace rrzc_tls_seed {
inline uint32_t Seed(uint64_t base, unsigned role) {
  uint32_t value=static_cast<uint32_t>((base+104729ULL*(role+1))&0x7fffffffULL);
  return value ? value : 1;
}
inline uint32_t Apply(uint64_t base, unsigned role) {
  const auto seed=Seed(base,role);
  *ROCKSDB_NAMESPACE::Random::GetTLSInstance()=ROCKSDB_NAMESPACE::Random(seed);
  return seed;
}
}
