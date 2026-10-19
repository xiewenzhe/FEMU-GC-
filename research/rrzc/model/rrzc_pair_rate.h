// 双点估速仅供独立影子验证；不覆盖原增长历史或真实选区证据。
#pragma once
#include "rrzc_short_shadow.h"
#include <deque>
#include <mutex>

namespace rrzc_v1 {
inline bool PairRateEnabled(const char* value,bool sampled,bool observe,bool execute) {
  return value && std::strcmp(value,"1")==0 && sampled && observe && !execute;
}
struct PairFrames { std::deque<RecentReport> reports; uint64_t sequence=0; };
class PairRateHistory {
 public:
  void Publish(const RecentReport& report,bool good) {
    std::lock_guard<std::mutex> lock(mutex_);
    ++frames_.sequence;
    if (!good) { frames_.reports.clear(); return; }
    if (!frames_.reports.empty()) {
      const auto& last=frames_.reports.back();
      if (last.session_hi!=report.session_hi || last.session_lo!=report.session_lo ||
          last.threshold!=report.threshold || last.blocks_per_zone!=report.blocks_per_zone)
        frames_.reports.clear();
    }
    frames_.reports.push_back(report);
    if (frames_.reports.size()>2) frames_.reports.pop_front();
  }
  PairFrames Freeze() {
    std::lock_guard<std::mutex> lock(mutex_); return frames_;
  }
 private:
  std::mutex mutex_;
  PairFrames frames_;
};
struct PairRateEvidence {
  bool known=false,urgent=false;
  uint64_t delta=0,interval_ns=0,old_age_ns=0,new_age_ns=0,predicted_ns=UINT64_MAX;
  const char* reason="缺少最近两个采样点";
};
inline bool PairIdentity(const RecentReport& x,const RecentBlock& a,
                         const RecentReport& y,const RecentBlock& b) {
  return x.session_hi==y.session_hi && x.session_lo==y.session_lo &&
      x.threshold==y.threshold && x.blocks_per_zone==y.blocks_per_zone &&
      a.zone==b.zone && a.zone_index==b.zone_index && a.generation==b.generation &&
      a.epoch_start_ns==b.epoch_start_ns && a.block_id==b.block_id &&
      a.ch==b.ch && a.lun==b.lun && a.plane==b.plane && a.block==b.block &&
      (a.flags & kEnabled) && (b.flags & kEnabled);
}
inline PairRateEvidence CheckPairRate(const PairFrames& frames,
    const RecentReport& current,const RecentBlock& block,bool usable,uint64_t horizon_ns) {
  PairRateEvidence out;
  if (!usable || !current.threshold || !horizon_ns || horizon_ns>300*kSecond ||
      !(block.flags & kEnabled)) { out.reason="当前统计或参数无效"; return out; }
  if (frames.reports.size()!=2) return out;
  const auto& old=frames.reports.front(); const auto& last=frames.reports.back();
  auto find=[&](const RecentReport& r) {
    return std::find_if(r.zones.begin(),r.zones.end(),
        [&](const RecentBlock& b){return b.zone==block.zone;});
  };
  const auto a=find(old),b=find(last);
  if (a==old.zones.end() || b==last.zones.end()) { out.reason="端点区缺失"; return out; }
  if (!PairIdentity(old,*a,last,*b) || !PairIdentity(last,*b,current,block)) {
    out.reason="会话代际或块身份变化"; return out;
  }
  if (a->epoch_start_ns>a->snapshot_ns || a->snapshot_ns>=b->snapshot_ns ||
      b->snapshot_ns>=block.snapshot_ns || a->rc>b->rc || b->rc>block.rc) {
    out.reason="时间或读数不单调"; return out;
  }
  out.interval_ns=b->snapshot_ns-a->snapshot_ns;
  out.old_age_ns=block.snapshot_ns-a->snapshot_ns;
  out.new_age_ns=block.snapshot_ns-b->snapshot_ns;
  if (out.interval_ns<kGrowthMinNs) { out.reason="双点间隔过近"; return out; }
  if (out.interval_ns>kGrowthMaxNs || out.old_age_ns>kGrowthMaxNs) {
    out.reason="双点间隔或最旧端点过旧"; return out;
  }
  out.known=true; out.delta=b->rc-a->rc;
  if (block.rc>=current.threshold) {
    out.predicted_ns=0; out.urgent=true; out.reason="同块当前已达阈值"; return out;
  }
  if (!out.delta) { out.reason="双点窗口无读取"; return out; }
  const __uint128_t numerator=__uint128_t(current.threshold-block.rc)*out.interval_ns;
  const __uint128_t eta=(numerator+out.delta-1)/out.delta;
  out.predicted_ns=eta>=UINT64_MAX ? UINT64_MAX : uint64_t(eta);
  out.urgent=out.predicted_ns<=horizon_ns;
  out.reason=out.urgent ? "双点同块速率支持界限内达阈值" : "双点同块速率不足以支持界限内达阈值";
  return out;
}
// 仅把独立估速结果送入原短期影子排序，绝不伪装为旧增长日志。
inline GrowthEvidence PairAsShadowEvidence(const PairRateEvidence& pair) {
  GrowthEvidence out; out.known=pair.known; out.pass=pair.urgent;
  out.delta=pair.delta; out.interval_ns=pair.interval_ns;
  out.predicted_ns=pair.predicted_ns; out.reason=pair.reason; return out;
}
}  // 双点估速命名空间。
