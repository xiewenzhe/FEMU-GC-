// 独立原型的真实Flush调用上下文；不是未来文件寿命预测。
#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>

namespace rrzc_flush_origin {
struct Context { bool active=false; uint64_t job=0; };
inline thread_local Context current;
inline bool Enabled() {
  const char* value=std::getenv("ZENFS_RRZC_POOL_FLUSH_W");
  if(!value || !std::strcmp(value,"0"))return false;
  if(!std::strcmp(value,"1"))return true;
  throw std::invalid_argument("Flush来源W开关只接受0或1");
}
class Scope {
 public:
  Scope(bool enabled,uint64_t job):previous_(current){current={enabled,job};}
  ~Scope(){current=previous_;}
  Scope(const Scope&)=delete;
  Scope& operator=(const Scope&)=delete;
 private:
  Context previous_;
};
}  // 仅当前线程BuildTable调用期间有效，结束和异常退出均恢复。
