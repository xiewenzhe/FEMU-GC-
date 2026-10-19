// 成本观察仅记录过去变化和当前尾部，不提供评分、改选或未来寿命预测。
#pragma once
#include "rrzc_recent_log.h"
#include <algorithm>
#include <cstring>
#include <deque>
#include <set>

namespace rrzc_cost {
using rrzc_v1::kSecond;
struct ValidityPoint {
  uint64_t zone=0, time_ns=0, valid=0, device_generation=0;
  uint64_t host_generation=0, host_sequence=0;
  bool known=false;
};
struct ValidityFrame {
  uint64_t session_hi=0, session_lo=0;
  std::vector<ValidityPoint> zones;
};
struct Change {
  bool known=false, has_previous=false;
  ValidityPoint previous;
  uint64_t interval_ns=0, decreased=0, increased=0;
  const char* reason="缺少历史采样";
};
inline bool Enabled(const char* value, bool short_shadow) {
  return short_shadow && value && std::strcmp(value,"1")==0;
}
inline bool SameEpoch(const ValidityPoint& a,const ValidityPoint& b) {
  return a.zone==b.zone && a.device_generation==b.device_generation &&
      a.host_generation==b.host_generation && a.host_sequence==b.host_sequence;
}
class ValidityHistory {
 public:
  void Clear() { frames_.clear(); }
  bool Push(ValidityFrame frame) {
    if (!(frame.session_hi || frame.session_lo) || frame.zones.empty()) { Clear(); return false; }
    std::set<uint64_t> seen;
    for (const auto& p:frame.zones)
      if (!seen.emplace(p.zone).second || !p.time_ns) { Clear(); return false; }
    if (!frames_.empty() && (frames_.back().session_hi!=frame.session_hi ||
        frames_.back().session_lo!=frame.session_lo)) Clear();
    if (!frames_.empty() && frames_.back().zones.front().time_ns>=frame.zones.front().time_ns) {
      Clear(); return false;
    }
    frames_.push_back(std::move(frame));
    if (frames_.size()>40) frames_.pop_front();
    return true;
  }
  Change Check(const ValidityFrame& current,const ValidityPoint& point,uint64_t window_ns) const {
    Change out;
    if (!point.known || (window_ns!=10*kSecond && window_ns!=30*kSecond) ||
        point.time_ns<window_ns) { out.reason="当前有效量或窗口未知"; return out; }
    const uint64_t target=point.time_ns-window_ns;
    for (auto f=frames_.rbegin();f!=frames_.rend();++f) {
      if (f->session_hi!=current.session_hi || f->session_lo!=current.session_lo) {
        out.reason="设备会话变化"; return out;
      }
      const auto p=std::find_if(f->zones.begin(),f->zones.end(),
          [&](const ValidityPoint& x){return x.zone==point.zone;});
      if (p==f->zones.end() || !p->known || !SameEpoch(*p,point)) {
        out.reason="历史区缺失或Reset观察变化"; return out;
      }
      if (p->time_ns>=point.time_ns) { out.reason="采样时间不递增"; return out; }
      if (p->time_ns>target) continue;
      out.has_previous=true; out.previous=*p;
      out.interval_ns=point.time_ns-p->time_ns;
      if (target-p->time_ns>2*kSecond) { out.reason="窗口端点采样过旧"; return out; }
      out.known=true;
      out.decreased=p->valid>point.valid ? p->valid-point.valid : 0;
      out.increased=point.valid>p->valid ? point.valid-p->valid : 0;
      out.reason="同区同代际有效量端点差，不区分删除与迁移";
      return out;
    }
    return out;
  }
 private:
  std::deque<ValidityFrame> frames_;
};
struct ReserveObservation {
  bool enabled=false, known=false, present=false;
  uint64_t zone=0, remaining=0, valid=0, wp=0;
  uint64_t host_generation=0, host_sequence=0;
  const char* reason="预留观察未启用";
};
struct PhysicalBounds { bool known=false; uint64_t lower=0,upper=0; };
inline PhysicalBounds BoundPhysical(uint64_t logical,uint64_t block_size,uint64_t sparse_header) {
  if (!logical || !block_size) return {};
  const __uint128_t low=(__uint128_t(logical)+block_size-1)/block_size*block_size;
  const __uint128_t high=(__uint128_t(logical)+sparse_header+block_size-1)/block_size*block_size;
  if (high>UINT32_MAX) return {};
  return {true,uint64_t(low),uint64_t(high)};
}
}  // 命名空间结束。
