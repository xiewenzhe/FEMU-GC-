// 配对准入的独立策略核；合成估计不是实际RR次数或已节省迁移量。
#pragma once
#include "rrzc_recent_log.h"
#include <algorithm>
#include <cmath>
#include <map>
#include <mutex>
#include <stdexcept>

namespace rrzc_pair {
constexpr uint64_t kHorizon = 10 * rrzc_v1::kSecond;
struct Live {
  uint64_t total=0, h=0, w=0, both=0, revision=0;
  // both已经同时包含在h和w内，不能再相加作为总有效量。
};
class Ledger {
 public:
  void Publish(const void* owner,uint8_t tag,const std::map<uint64_t,uint64_t>& extents) {
    if(tag>3)throw std::invalid_argument("配对账目标签非法");
    std::lock_guard<std::mutex> lock(mutex_);
    auto found=files_.find(owner);
    if(extents.empty()) {
      if(found!=files_.end()){files_.erase(found);++revision_;}
    } else if(found==files_.end() || found->second.tag!=tag || found->second.extents!=extents) {
      files_[owner]={tag,extents};++revision_;
    }
  }
  Live Read(uint64_t zone) const {
    std::lock_guard<std::mutex> lock(mutex_);Live out;out.revision=revision_;
    for(const auto& f:files_) {
      const auto p=f.second.extents.find(zone);if(p==f.second.extents.end())continue;
      const auto n=p->second;out.total+=n;
      if(f.second.tag&1)out.h+=n;
      if(f.second.tag&2)out.w+=n;
      if(f.second.tag==3)out.both+=n;
    }
    return out;
  }
  bool Unchanged(uint64_t revision) const {
    std::lock_guard<std::mutex> lock(mutex_);return revision==revision_;
  }
 private:
  struct File {uint8_t tag;std::map<uint64_t,uint64_t> extents;};
  mutable std::mutex mutex_;
  std::map<const void*,File> files_;
  uint64_t revision_=0;
};
struct Risk {
  bool known=false;
  uint64_t eta=UINT64_MAX;
  uint32_t block=0;
  uint64_t rc=0;
};
// 完整十秒窗口才使用速率；零增长只描述历史，不能证明未来无风险。
inline bool Rate(const rrzc_v1::RecentBlock& b,long double* per_ns) {
  if(!(b.flags&rrzc_v1::kEnabled) || !(b.flags&rrzc_v1::kValid10) ||
      (b.flags&rrzc_v1::kLoss) || b.snapshot_ns<b.epoch_start_ns ||
      b.snapshot_ns-b.epoch_start_ns<10*rrzc_v1::kSecond ||
      b.delta10>b.delta30 || b.delta30>b.rc)return false;
  *per_ns=static_cast<long double>(b.delta10)/(10*rrzc_v1::kSecond);
  if(b.flags&rrzc_v1::kValid30) {
    if(b.snapshot_ns-b.epoch_start_ns<30*rrzc_v1::kSecond)return false;
    *per_ns=std::max(*per_ns,static_cast<long double>(b.delta30)/(30*rrzc_v1::kSecond));
  }
  return true;
}
// 对全部块取最早ETA；迁入H的源区总速率仅是集中到一个目标块的保守情景。
// 它不是FTL映射预测，也不保证负载变化后的上界。
inline Risk Forecast(const std::vector<rrzc_v1::RecentBlock>& blocks,uint64_t threshold,
                     long double incoming_h_rate=0) {
  Risk out;if(blocks.empty() || !threshold || incoming_h_rate<0 ||
      !std::isfinite(incoming_h_rate))return out;
  const auto& first=blocks.front();bool all=true;
  for(const auto& b:blocks) {
    if(b.generation!=first.generation || b.snapshot_ns!=first.snapshot_ns ||
        b.epoch_start_ns!=first.epoch_start_ns || b.zone!=first.zone)return {};
    long double rate=0;bool known=Rate(b,&rate);
    if(b.rc>=threshold)known=true;
    if(!known){all=false;continue;}
    rate+=incoming_h_rate;
    const long double t=b.rc>=threshold?0:rate>0?std::ceil((threshold-b.rc)/rate):UINT64_MAX;
    const uint64_t eta=t>=UINT64_MAX?UINT64_MAX:static_cast<uint64_t>(t);
    if(eta<out.eta || (eta==out.eta && b.block_id<out.block)) {
      out.eta=eta;out.block=b.block_id;out.rc=b.rc;
    }
  }
  out.known=all;return out;
}
struct Input {
  Live live;
  uint64_t native_valid=0,remaining=0,required=0,budget=0;
  uint8_t incoming=0;
  bool new_file=false,incoming_h_known=false;
  Risk risk;
};
struct Decision {
  bool allow=false,mixed=false;
  uint64_t companion_upper=0;
  const char* reason="有效量账目不完整";
};
inline Decision Evaluate(const Input& i) {
  Decision out;
  if(!i.incoming || i.incoming>3 || !i.required || i.required>i.remaining ||
      i.live.total!=i.native_valid || i.live.h>i.live.total || i.live.w>i.live.total ||
      i.live.both>std::min(i.live.h,i.live.w))return out;
  const bool h=i.live.h || (i.incoming&1),w=i.live.w || (i.incoming&2);
  out.mixed=h&&w;
  if(!out.mixed){out.allow=true;out.reason="本次不形成H/W共区";return out;}
  // 对新文件尚未知大小，使用本目标剩余容量作为本次加入W的上界。
  const uint64_t incoming_w=(i.incoming&2)?(i.new_file?i.remaining:i.required):0;
  if(incoming_w>UINT64_MAX-i.live.w)return out;
  const uint64_t upper=i.live.w+incoming_w;
  out.companion_upper=upper;
  if((i.incoming&1) && !i.incoming_h_known) {
    out.reason="迁入H的读取压力未知，不承诺配对收益";return out;
  }
  if(!i.risk.known){out.reason="逐块近期风险不完整，不承诺配对收益";return out;}
  if(i.risk.eta<=kHorizon && upper>i.budget) {
    out.reason="近期RR可能连带复制的W上界超过预算";return out;
  }
  out.allow=true;out.reason=i.risk.eta<=kHorizon?"近期伴随复制在预算内":"完整窗口下预计RR在观察窗外";
  return out;
}
}  // 配对准入命名空间结束。
