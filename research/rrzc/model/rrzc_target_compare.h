// 必要H迁移的双目标比较；只使用当时账目，不冒充未来净收益预测。
#pragma once
#include "rrzc_pair_admission.h"

namespace rrzc_target {
struct Target {
  uint64_t zone=0,native_valid=0,remaining=0;
  rrzc_pair::Live live;
  bool available=false;
};
struct Decision {
  bool known=false,ordinary=false;
  uint64_t pool_companion=0,ordinary_companion=0;
  const char* reason="替代目标或账目不可验证，保留协同目标";
};
inline bool Valid(const Target& t,uint64_t required) {
  return t.available && required && t.remaining>=required &&
      t.native_valid==t.live.total && t.live.h<=t.live.total &&
      t.live.w<=t.live.total && t.live.both<=std::min(t.live.h,t.live.w);
}
inline Decision Compare(const Target& pool,const Target& ordinary,uint64_t required) {
  Decision out;
  if(!Valid(pool,required) || !Valid(ordinary,required) || pool.zone==ordinary.zone ||
      pool.live.revision!=ordinary.live.revision)return out;
  out.known=true;
  // 所有非H来源均计价，包括Compaction及无标签文件；不只计算W。
  out.pool_companion=pool.live.total-pool.live.h;
  out.ordinary_companion=ordinary.live.total-ordinary.live.h;
  if(out.ordinary_companion>=out.pool_companion) {
    out.reason="普通目标现有伴随数据不少，保留协同目标";return out;
  }
  // 剩余空间是未来填充的容量上界，不假定一定填满或一定存活。
  if(ordinary.remaining>pool.remaining) {
    out.reason="普通目标未来可填充空间更大，保留协同目标";return out;
  }
  out.ordinary=true;out.reason="普通目标伴随量更少且可填充空间不更大";
  return out;
}
}  // 双目标比较命名空间结束。
