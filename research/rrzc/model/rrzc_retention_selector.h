// 实验版净维护收益：近期紧迫度准入不变，仅在可信历史上估计下次检查时的有效量。
#pragma once
#include "rrzc_cost_rank.h"
#include "rrzc_migration_window.h"
#include <deque>
#include <mutex>

namespace rrzc_retention {
using rrzc_v1::kSecond;
struct Point {
  uint64_t zone=0, time=0, valid=0, device_generation=0;
  uint64_t host_generation=0, host_sequence=0;
  bool known=false;
};
struct Frame {
  uint64_t session_hi=0, session_lo=0;
  std::map<uint64_t,Point> zones;
  rrzc_observe::MigrationWindow::Frame before,after;
};
struct Forecast {
  bool known=false;
  uint64_t retained=0, loss10=0, loss30=0, interval10=0, interval30=0;
  const char* reason="缺少连续满区历史";
};
inline bool SameEpoch(const Point& a,const Point& b) {
  return a.zone==b.zone && a.device_generation==b.device_generation &&
      a.host_generation==b.host_generation && a.host_sequence==b.host_sequence;
}
class History {
 public:
  void Clear() { frames_.clear(); }
  void Push(Frame frame) {
    if (!(frame.session_hi || frame.session_lo) || frame.zones.empty()) { Clear(); return; }
    if (!frames_.empty() && (frames_.back().session_hi!=frame.session_hi ||
        frames_.back().session_lo!=frame.session_lo ||
        frames_.back().after.captured_ns>=frame.after.captured_ns)) Clear();
    frames_.push_back(std::move(frame));
    if (frames_.size()>40) frames_.pop_front();
  }
  Forecast Check(const Frame& current,const Point& point,uint64_t horizon) const {
    Forecast out;
    if (!point.known || horizon!=10*kSecond) return out;
    const Frame* anchors[2]={nullptr,nullptr};
    const Point* previous[2]={nullptr,nullptr};
    uint64_t last_time=point.time,last_valid=point.valid;
    for (auto it=frames_.rbegin();it!=frames_.rend();++it) {
      const auto p=it->zones.find(point.zone);
      if (it->session_hi!=current.session_hi || it->session_lo!=current.session_lo ||
          p==it->zones.end() || !p->second.known || !SameEpoch(p->second,point)) {
        out.reason="历史缺失、非满区、忙或Reset会话变化"; return out;
      }
      const auto& old=p->second;
      if (old.time>=last_time || last_time-old.time>2500000000ULL || old.valid<last_valid) {
        out.reason="历史不连续或有效量曾增加"; return out;
      }
      last_time=old.time; last_valid=old.valid;
      const uint64_t age=point.time-old.time;
      for (unsigned w=0;w<2;++w) {
        const uint64_t window=(w ? 30 : 10)*kSecond;
        if (!anchors[w] && age>=window) {
          if (age-window>2*kSecond) { out.reason="窗口端点过旧"; return out; }
          anchors[w]=&*it; previous[w]=&old;
        }
      }
      if (anchors[1]) break;
    }
    if (!anchors[0] || !anchors[1]) return out;
    const auto clean=rrzc_observe::MigrationWindow::Check(
        anchors[1]->before,current.after,point.zone,previous[1]->time,point.time);
    if (!clean.known || !clean.clean) { out.reason=clean.reason; return out; }
    out.loss10=previous[0]->valid-point.valid; out.loss30=previous[1]->valid-point.valid;
    out.interval10=point.time-previous[0]->time; out.interval30=point.time-previous[1]->time;
    // 取两段历史退役速率较小者；这只是线性外推假设，不是已知文件寿命。
    const __uint128_t a=__uint128_t(out.loss10)*horizon/out.interval10;
    const __uint128_t b=__uint128_t(out.loss30)*horizon/out.interval30;
    const uint64_t loss=uint64_t(std::min<__uint128_t>(point.valid,std::min(a,b)));
    out.retained=point.valid-loss; out.known=true;
    out.reason="同代际连续满区无迁移，两窗口较低退役速率外推十秒";
    return out;
  }
 private:
  std::deque<Frame> frames_;
};
class Store {
 public:
  void Publish(Frame frame,bool good) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (good) history_.Push(std::move(frame)); else history_.Clear();
  }
  History Freeze() { std::lock_guard<std::mutex> lock(mutex_); return history_; }
 private:
  std::mutex mutex_;
  History history_;
};
inline rrzc_v1::ShadowDecision Evaluate(
    const std::vector<rrzc_simple::Candidate>& candidates,
    const std::map<uint64_t,rrzc_v1::RecentBlock>& usable,
    const std::map<uint64_t,rrzc_v1::GrowthEvidence>& growth,
    const std::map<uint64_t,Forecast>& forecasts,
    uint64_t threshold,uint64_t reclaim,uint64_t horizon,uint64_t budget) {
  auto out=rrzc_v1::EvaluateShortShadow(candidates,usable,growth,threshold,reclaim,horizon,budget);
  if (!out.would_switch) return out;
  out.would_switch=false; out.proposed_zone=out.greedy.selected.zone;
  out.extra_valid=0; out.predicted_ns=0;
  out.reason="没有可信且净维护收益为正的紧迫候选";
  uint64_t best_benefit=0,best_valid=UINT64_MAX;
  for (const auto& c:candidates) {
    if (!rrzc_simple::Eligible(c,reclaim) || c.zone==out.greedy.selected.zone) continue;
    const auto b=usable.find(c.zone); const auto g=growth.find(c.zone); const auto f=forecasts.find(c.zone);
    if (b==usable.end() || g==growth.end() || f==forecasts.end() || !f->second.known || f->second.retained>c.valid) continue;
    const auto eta=rrzc_v1::ShortTimeToThreshold(b->second,g->second,threshold);
    const uint64_t extra=c.valid>out.greedy.selected.valid ? c.valid-out.greedy.selected.valid : 0;
    if (!eta.known || eta.threshold_ns>horizon || extra>budget || f->second.retained<=extra) continue;
    const uint64_t benefit=f->second.retained-extra;
    if (!out.would_switch || benefit>best_benefit || (benefit==best_benefit &&
        (c.valid<best_valid || (c.valid==best_valid && (eta.threshold_ns<out.predicted_ns ||
         (eta.threshold_ns==out.predicted_ns && c.zone<out.proposed_zone)))))) {
      out.would_switch=true; out.proposed_zone=c.zone; out.extra_valid=extra;
      out.predicted_ns=eta.threshold_ns; best_benefit=benefit; best_valid=c.valid;
      out.reason="预计保留有效量减额外迁移量最大且为正";
    }
  }
  return out;
}
}  // 留存收益实验命名空间。
