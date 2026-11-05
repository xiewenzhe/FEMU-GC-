// C4h逐Zone整帧查询；保留每个物理块的身份和真实窗口，不改C2h。
#pragma once
#include "rrzc_joint_time_selector.h"
#include <map>
#include <set>
#include <tuple>

namespace rrzc_all_blocks {
using rrzc_v1::RecentBlock;
constexpr uint64_t kSecond=rrzc_v1::kSecond;
struct Report {
  uint64_t threshold=0,session_hi=0,session_lo=0;
  uint32_t nr_zones=0,blocks_per_zone=0;
  std::vector<RecentBlock> blocks;
};
inline bool Decode(const uint8_t* data,size_t size,uint32_t zones,uint32_t count,
                   uint32_t index,uint64_t zone_size,uint32_t lba_size,
                   Report* output,std::string* error) {
  *output={};
  auto fail=[&](const char* why){*error=why;return false;};
  using rrzc_v1::Little;
  if(!data || !zones || index>=zones || !count || !zone_size || !lba_size ||
      zone_size%lba_size || uint64_t(index)>UINT64_MAX/zone_size ||
      count>(SIZE_MAX-56)/88 || size!=56+size_t(count)*88)
    return fail("C4h几何或缓冲长度错误");
  if(Little(data,4)!=1 || Little(data+4,4)!=88 || Little(data+8,4)!=zones ||
      Little(data+12,4)!=count || Little(data+40,8)!=10*kSecond || Little(data+48,8)!=30*kSecond)
    return fail("C4h协议或几何不匹配");
  Report r;r.nr_zones=zones;r.blocks_per_zone=count;r.threshold=Little(data+16,8);
  r.session_hi=Little(data+24,8);r.session_lo=Little(data+32,8);
  if(!r.threshold || !(r.session_hi || r.session_lo))return fail("C4h阈值或会话无效");
  std::set<std::tuple<uint32_t,uint32_t,uint32_t,uint32_t>> addresses;
  for(uint32_t id=0;id<count;++id) {
    const auto* p=data+56+size_t(id)*88;
    RecentBlock b;b.zone=uint64_t(index)*zone_size;b.zone_index=Little(p+56,4);
    b.generation=Little(p+8,8);b.snapshot_ns=Little(p+16,8);b.epoch_start_ns=Little(p+24,8);
    b.rc=Little(p+32,8);b.delta10=Little(p+40,8);b.delta30=Little(p+48,8);
    b.block_id=Little(p+60,4);b.ch=Little(p+64,4);b.lun=Little(p+68,4);
    b.plane=Little(p+72,4);b.block=Little(p+76,4);b.flags=Little(p+80,4);
    if(Little(p,8)!=b.zone/lba_size || b.zone_index!=index || b.block_id!=id ||
        !(b.flags&rrzc_v1::kEnabled) || (b.flags&~uint32_t(15)) || Little(p+84,4) ||
        b.snapshot_ns<b.epoch_start_ns || b.delta10>b.delta30 || b.delta30>b.rc ||
        !addresses.emplace(b.ch,b.lun,b.plane,b.block).second)
      return fail("C4h块身份、计数或标志错误");
    const uint64_t age=b.snapshot_ns-b.epoch_start_ns;
    if(((b.flags&rrzc_v1::kValid10) && age<10*kSecond) ||
        ((b.flags&rrzc_v1::kValid30) && (age<30*kSecond || !(b.flags&rrzc_v1::kValid10))) ||
        ((b.flags&rrzc_v1::kLoss) && (b.flags&rrzc_v1::kValid30)))
      return fail("C4h窗口标志矛盾");
    if(id && (b.generation!=r.blocks[0].generation || b.snapshot_ns!=r.blocks[0].snapshot_ns ||
        b.epoch_start_ns!=r.blocks[0].epoch_start_ns))return fail("C4h不是同代际同时间整帧");
    r.blocks.push_back(b);
  }
  *output=std::move(r);error->clear();return true;
}
// 每个Block单独保留原时序核；最大RC块换身份不会破坏其他块的历史。
class History {
 public:
  void Clear(){frames_.clear();}
  void Push(const Report& r) {
    if(r.blocks.empty()){Clear();return;}
    if(!frames_.empty()) {
      const auto& old=frames_.back();
      if(old.session_hi!=r.session_hi || old.session_lo!=r.session_lo ||
          old.threshold!=r.threshold || old.nr_zones!=r.nr_zones ||
          old.blocks_per_zone!=r.blocks_per_zone || old.blocks[0].zone!=r.blocks[0].zone)Clear();
    }
    frames_.push_back(r);if(frames_.size()>4)frames_.pop_front();
  }
  rrzc_v1::GrowthEvidence Check(const Report& current,const RecentBlock& b,uint64_t horizon) const {
    rrzc_deadline::History one;
    for(const auto& r:frames_) {
      rrzc_v1::RecentReport frame;frame.threshold=r.threshold;frame.session_hi=r.session_hi;
      frame.session_lo=r.session_lo;frame.blocks_per_zone=r.blocks_per_zone;
      for(const auto& old:r.blocks)if(old.block_id==b.block_id)frame.zones.push_back(old);
      one.Push(frame);
    }
    rrzc_v1::RecentReport frame;frame.threshold=current.threshold;frame.session_hi=current.session_hi;
    frame.session_lo=current.session_lo;frame.blocks_per_zone=current.blocks_per_zone;
    return one.Check(frame,b,horizon);
  }
 private:
  std::deque<Report> frames_;
};
class Store {
 public:
  using Histories=std::map<uint64_t,History>;
  void Publish(const std::vector<Report>& reports) {
    std::lock_guard<std::mutex> guard(mutex_);std::set<uint64_t> present;
    for(const auto& r:reports)if(!r.blocks.empty()) {
      const auto zone=r.blocks[0].zone;histories_[zone].Push(r);present.insert(zone);
    }
    // 本轮缺失或失败不能跨越到更旧的成功帧。
    for(auto p=histories_.begin();p!=histories_.end();) {
      if(!present.count(p->first))p=histories_.erase(p);else ++p;
    }
  }
  Histories Freeze(){std::lock_guard<std::mutex> guard(mutex_);return histories_;}
 private:
  std::mutex mutex_;Histories histories_;
};
struct Evidence {
  struct BlockEvidence {RecentBlock block;rrzc_direct::Cost cost;rrzc_v1::GrowthEvidence growth;};
  bool selected=false,complete=false;
  RecentBlock block;
  uint32_t observed=0,known=0;
  rrzc_joint_time::Evidence joint;
  std::vector<BlockEvidence> distribution;
};
// 先以同块2~4秒及十秒窗口较慢速率计算ETA，再取Zone最早者；不叠加多块RR抵扣。
inline rrzc_direct::Cost Estimate(const rrzc_simple::Candidate& c,const Report* r,
    const History* history,const rrzc_window_credit::Retention* retention,
    uint64_t horizon,Evidence* evidence) {
  auto& e=*evidence;e={};rrzc_direct::Cost best;best.net=c.valid;
  if(c.valid<c.capacity)best.reclaimed=c.capacity-c.valid;
  if(!r || r->blocks.empty() || r->blocks.size()!=r->blocks_per_zone ||
      r->blocks[0].zone!=c.zone)return best;
  e.observed=r->blocks.size();bool every=true;
  for(const auto& b:r->blocks) {
    const auto growth=history?history->Check(*r,b,horizon):rrzc_v1::GrowthEvidence{};
    rrzc_joint_time::Evidence proof;
    const auto cost=rrzc_joint_time::EstimateEvidence(c,&b,retention,r->threshold,horizon,growth,&proof);
    e.distribution.push_back({b,cost,growth});
    if(!cost.risk_known){every=false;continue;}
    ++e.known;
    if(!e.selected || cost.eta<best.eta || (cost.eta==best.eta && b.block_id<e.block.block_id)) {
      best=cost;e.block=b;e.joint=proof;e.selected=true;
    }
  }
  e.complete=every;
  // 未知块可能更紧急；保留已知块估计，但明确披露不完整，不伪称全Zone已安全。
  return best;
}
}  // 全块维护风险命名空间结束。
