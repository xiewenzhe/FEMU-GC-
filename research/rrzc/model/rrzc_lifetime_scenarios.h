// 仅选择预注册的逻辑写Key范围；不向放置策略传递未来寿命标签。
#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>

namespace rrzc_lifetime_scenarios {
enum class Scenario { kOriginal, kRepeat, kSpread, kSwitch };
inline Scenario Parse(const char* value) {
  if(!value)return Scenario::kOriginal;
  if(!std::strcmp(value,"repeat"))return Scenario::kRepeat;
  if(!std::strcmp(value,"spread"))return Scenario::kSpread;
  if(!std::strcmp(value,"switch"))return Scenario::kSwitch;
  throw std::invalid_argument("生命周期场景仅接受repeat、spread或switch");
}
inline Scenario Selected() {
  static const auto selected=Parse(std::getenv("RRZC_LIFETIME_SCENARIO"));
  return selected;
}
inline uint64_t Slot(uint64_t window) {
  if(window>=12)throw std::invalid_argument("生命周期场景窗口越界");
  switch(Selected()) {
    case Scenario::kRepeat:return 0;
    case Scenario::kSpread:return window;
    case Scenario::kSwitch:return window<6?0:window-5;
    default:return window%6;
  }
}
}  // 未选择场景时保持旧12窗口原输入；一个进程内不可切换。
