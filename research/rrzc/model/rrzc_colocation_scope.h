// 仅供独立协同池原型传递显式受控标签和本次迁移源；不预测未来文件寿命。
#pragma once
#include "rrzc_colocation_pool.h"
namespace rrzc_colocation {
struct Context {Tag tag=Tag::kNormal;std::set<uint64_t> sources;};
inline thread_local Context current;
class Scope {
 public:
  explicit Scope(uint8_t tag,std::set<uint64_t> sources={}):old_(current) {
    current={static_cast<Tag>(tag),std::move(sources)};
  }
  ~Scope(){current=std::move(old_);}
  Scope(const Scope&)=delete;
  Scope& operator=(const Scope&)=delete;
 private:Context old_;
};
}
