// 只在一次必要迁移调用内减少H的普通区陪迁，不创建专用池或持久化标签。
#pragma once
#include "rrzc_placement_mode.h"
#include <cstdint>
#include <map>
#include <set>
#include <tuple>

namespace rrzc_hot_fallback {
inline bool Enabled(rrzc_placement::Mode mode) {
  return mode==rrzc_placement::Mode::kG2HotFallback;
}
class Batch;
inline thread_local Batch* current=nullptr;
class Batch {
 public:
  Batch(bool enabled,uint64_t device):old_(current),device_(device) {
    if(enabled)current=this;
    else current=nullptr;
  }
  ~Batch(){current=old_;}
  Batch(const Batch&)=delete;
  Batch& operator=(const Batch&)=delete;
  uint64_t Device() const {return device_;}
  void Sources(const std::set<uint64_t>& sources){sources_=sources;}
  bool Source(uint64_t zone) const {return sources_.count(zone)!=0;}
  void Hot(uint64_t zone,uint64_t generation){hot_[zone]=generation;}
  bool Avoid(uint64_t zone,uint64_t generation) const {
    auto it=hot_.find(zone);return it!=hot_.end() && it->second==generation;
  }
 private:
  Batch* old_;
  uint64_t device_;
  std::set<uint64_t> sources_;
  std::map<uint64_t,uint64_t> hot_;
};

// H优先减少当前已有有效引用；其他文件只在有替代时避开本批H目标。
// 排名不能把容量、内容、寿命资格或独占失败的Zone变成合格目标。
using Rank=std::tuple<uint64_t,uint64_t,uint64_t,uint64_t>;
inline Rank Ranking(uint8_t tag,bool avoid,uint64_t used,unsigned diff,uint64_t zone) {
  return tag&1 ? Rank{used,diff,zone,0} : Rank{avoid?1UL:0UL,diff,UINT64_MAX-zone,0};
}
}  // 本批迁移选择命名空间结束。
