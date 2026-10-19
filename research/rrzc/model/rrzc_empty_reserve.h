// 空预留周转只维持公共进展条件，不参与victim评分。
#pragma once
#include <cstring>
namespace rrzc_empty {
inline bool Enabled(const char* value,bool recovery) {
  return recovery && value && !std::strcmp(value,"1");
}
struct Rotation { bool rotate=false,need_token=false; };
inline Rotation Plan(bool migrating,bool present,bool empty,bool full) {
  if (migrating || (present && empty)) return {};
  return {true,present && !full};
}
}  // 命名空间结束。
