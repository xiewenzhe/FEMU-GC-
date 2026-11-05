// 协同池零有效数据尾部的最小保留条件；不发起迁移或改变RR阈值。
#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>

namespace rrzc_pool_tail {
inline bool Flag(const char* name) {
  const char* p=std::getenv(name);
  if(p && std::strcmp(p,"0") && std::strcmp(p,"1"))
    throw std::invalid_argument("尾部管理开关只能为0或1");
  return p && !std::strcmp(p,"1");
}
inline bool Enabled(){return Flag("ZENFS_RRZC_POOL_TAIL_REUSE");}
inline bool Observe(){return Enabled() || Flag("ZENFS_RRZC_POOL_TAIL_OBSERVE");}
struct Input {
  bool member=false,used=false,empty=false,full=false,stable=false,risk_known=false;
  bool migrating=false,reserve_ready=false;
  uint64_t remaining=0,alignment=0,rc=0,threshold=0,waiters=0,active=0,limit=0;
};
inline bool Keep(const Input& x) {
  return x.member && !x.used && !x.empty && !x.full && x.stable && x.risk_known &&
      x.alignment && x.remaining>=x.alignment && x.threshold && x.rc<x.threshold &&
      !x.migrating && x.reserve_ready && !x.waiters && x.active<x.limit;
}
}  // 条件核仅判断是否保留；实际独占权与令牌由ZenFS管理。
