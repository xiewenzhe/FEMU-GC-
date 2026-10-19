// 只交换两个部分区的管理归属，不迁移、Finish或Reset数据。
#pragma once
#include <cstdint>
#include <cstring>
namespace rrzc_exchange {
inline bool Enabled(const char* value,bool recovery) {
  return recovery && value && !std::strcmp(value,"1");
}
struct State { uint64_t zone=0,remaining=0,capacity=0,valid=0,wp=0; };
inline bool Partial(const State& s) {
  return s.capacity && s.remaining && s.remaining<s.capacity && s.wp>s.zone &&
      __uint128_t(s.wp-s.zone)+s.remaining==s.capacity &&
      __uint128_t(s.valid)+s.remaining<=s.capacity;
}
inline bool CanExchange(uint64_t required,const State& old,const State& next) {
  return required && Partial(old) && Partial(next) && next.valid && old.zone!=next.zone &&
      old.remaining<required && required<=next.remaining;
}
}  // 命名空间结束。
