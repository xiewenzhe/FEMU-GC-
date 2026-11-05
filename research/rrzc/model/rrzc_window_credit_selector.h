// 窗口自适应净维护成本核；独立于旧评分，不改候选、RR阈值或GC触发。
#pragma once
#include "rrzc_direct_selector.h"

namespace rrzc_window_credit {
using rrzc_v1::kSecond;
using Row=rrzc_direct::Row;

struct Retention {
  bool known=false;
  uint64_t zone=0,generation=0,valid=0;
  uint64_t short_loss=0,short_span=0,long_loss=0,long_span=0;
  const char* reason="没有足够的同代际满区观察";
};

// 保留最近约三十秒的原始观察，允许使用真实可见的连续前缀，不强求完整三十秒。
class History {
 public:
  void Clear() { frames_.clear(); }
  void Push(rrzc_retention::Frame frame) {
    if (!(frame.session_hi || frame.session_lo) || frame.zones.empty()) { Clear(); return; }
    if (!frames_.empty() && (frames_.back().session_hi!=frame.session_hi ||
        frames_.back().session_lo!=frame.session_lo ||
        frames_.back().after.captured_ns>=frame.after.captured_ns)) Clear();
    frames_.push_back(std::move(frame));
    if (frames_.size()>40) frames_.pop_front();
  }
  Retention Check(const rrzc_retention::Frame& current,const rrzc_retention::Point& point) const {
    Retention out;out.zone=point.zone;out.generation=point.device_generation;out.valid=point.valid;
    if (!point.known) return out;
    uint64_t last_time=point.time,last_valid=point.valid;
    for (auto it=frames_.rbegin();it!=frames_.rend();++it) {
      const auto p=it->zones.find(point.zone);
      if (it->session_hi!=current.session_hi || it->session_lo!=current.session_lo ||
          p==it->zones.end() || p->second.device_generation!=point.device_generation) break;
      const auto& old=p->second;
      // 临时Acquire失败不直接清空已有证据；仍由已知点间隔和迁移标记限制跨越。
      if (!old.known) continue;
      if (!rrzc_retention::SameEpoch(old,point) || old.time>=last_time ||
          last_time-old.time>2500000000ULL || old.valid<last_valid) break;
      const uint64_t span=point.time-old.time;
      if (span>32*kSecond) break;
      const auto clean=rrzc_observe::MigrationWindow::Check(
          it->before,current.after,point.zone,old.time,point.time);
      if (!clean.known || !clean.clean) break;
      last_time=old.time;last_valid=old.valid;
      if (!out.short_span || out.short_span<10*kSecond) {
        out.short_span=span;out.short_loss=old.valid-point.valid;
      }
      out.long_span=span;out.long_loss=old.valid-point.valid;
      if (span>=30*kSecond) break;
    }
    out.known=out.long_span>=rrzc_v1::kGrowthMinNs;
    if (out.known) out.reason="使用可验证的连续满区历史，历史不足不等同于未来全部存活";
    return out;
  }
 private:
  std::deque<rrzc_retention::Frame> frames_;
};

class Store {
 public:
  struct Snapshot {History history;uint64_t sequence=0;};
  uint64_t Publish(const rrzc_retention::Frame& frame,bool good) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (good) history_.Push(frame); else history_.Clear();
    return ++sequence_;
  }
  Snapshot Freeze() { std::lock_guard<std::mutex> lock(mutex_);return {history_,sequence_}; }
 private:
  History history_;
  uint64_t sequence_=0;
  std::mutex mutex_;
};

struct Evidence {
  uint64_t read_span=0,read_delta=0,window=0;
  const char* reason="统计或参数无效";
};
inline uint64_t Eta(uint64_t margin,uint64_t span,uint64_t reads) {
  if (!reads) return UINT64_MAX;
  const auto value=(__uint128_t(margin)*span+reads-1)/reads;
  return value>=UINT64_MAX ? UINT64_MAX : uint64_t(value);
}

inline rrzc_direct::Cost Estimate(const rrzc_simple::Candidate& c,const rrzc_v1::RecentBlock* b,
    const Retention* f,uint64_t threshold,uint64_t horizon,Evidence* evidence=nullptr) {
  Evidence local;auto& proof=evidence ? *evidence : local;proof={};
  rrzc_direct::Cost out;out.net=c.valid;
  if (c.valid>=c.capacity) return out;
  out.reclaimed=c.capacity-c.valid;
  if (!b || b->zone!=c.zone || !threshold || horizon!=10*kSecond ||
      !(b->flags&rrzc_v1::kEnabled) || (b->flags&rrzc_v1::kLoss) ||
      (b->flags&~uint32_t(15)) || b->epoch_start_ns>b->snapshot_ns ||
      b->delta10>b->delta30 || b->delta30>b->rc) return out;
  const uint64_t age=b->snapshot_ns-b->epoch_start_ns;
  if (((b->flags&rrzc_v1::kValid10) && age<10*kSecond) ||
      ((b->flags&rrzc_v1::kValid30) && (age<30*kSecond || !(b->flags&rrzc_v1::kValid10)))) return out;
  if (b->rc>=threshold) {
    out.risk_known=true;out.eta=0;out.retained=out.credit=c.valid;out.net=0;
    proof.reason="已到期RR需求，不计作提前预测成功";return out;
  }
  out.risk_known=true;
  if (b->delta10) {
    const bool complete=b->flags&rrzc_v1::kValid10;
    if ((!complete && (age>=10*kSecond || b->delta10!=b->rc)) || !age) {
      out.risk_known=false;proof.reason="十秒历史不完整且不能确认完整Reset后前缀";return out;
    }
    proof.read_span=std::min(age,10*kSecond);proof.read_delta=b->delta10;
  } else {
    // 完整十秒已无读取时，不用更旧的三十秒热度继续预支RR收益。
    proof.reason="真实十秒窗口无增长，不用三十秒旧热度抵扣";return out;
  }
  // 使用设备给出的当前同一物理块真实增量，不做两次Zone最大值相减。
  out.eta=Eta(threshold-b->rc,proof.read_span,proof.read_delta);
  if (!f || !f->known || f->zone!=c.zone || f->generation!=b->generation || f->valid!=c.valid ||
      !f->short_span || f->short_span>f->long_span || f->long_span>32*kSecond ||
      f->long_span<rrzc_v1::kGrowthMinNs || f->short_loss>f->long_loss ||
      f->long_loss>c.capacity-c.valid) {
    proof.reason="无可验证留存前缀，保留未知而非假设全部存活";return out;
  }
  auto loss=[&](uint64_t bytes,uint64_t span) {
    return std::min<__uint128_t>(c.valid,(__uint128_t(bytes)*out.eta+span-1)/span);
  };
  out.retained=c.valid-uint64_t(std::max(loss(f->short_loss,f->short_span),loss(f->long_loss,f->long_span)));
  // 收益只支撑到维护尺度、读历史和留存历史共同覆盖的最短窗口。
  // 该连续折减是调度效用，不是经过校准的RR概率，也不预测下一次ZC时间。
  proof.window=std::min({horizon,proof.read_span,f->long_span});
  if (out.eta>=proof.window) {proof.reason="预计RR超出可支撑窗口，不提前抵扣远期维护";return out;}
  out.credit=uint64_t(__uint128_t(out.retained)*(proof.window-out.eta)/(__uint128_t(proof.window)+out.eta));
  out.net=c.valid-out.credit;
  proof.reason=out.retained ? "同块真实读速率与短历史留存的窗口内净收益" : "预计RR前有效量已退役";
  return out;
}

inline std::vector<Row> Rank(const std::vector<rrzc_simple::Candidate>& candidates,
    const std::map<uint64_t,rrzc_v1::RecentBlock>& blocks,const std::map<uint64_t,Retention>& forecasts,
    uint64_t threshold,uint64_t reclaim,uint64_t horizon) {
  std::vector<Row> rows;
  for (const auto& c:candidates) {
    if (!rrzc_simple::Eligible(c,reclaim)) continue;
    const auto b=blocks.find(c.zone);const auto f=forecasts.find(c.zone);
    rows.push_back({c,Estimate(c,b==blocks.end()?nullptr:&b->second,
        f==forecasts.end()?nullptr:&f->second,threshold,horizon),false});
  }
  std::sort(rows.begin(),rows.end(),rrzc_direct::Less);return rows;
}
}  // 窗口自适应净维护成本实验命名空间结束。
