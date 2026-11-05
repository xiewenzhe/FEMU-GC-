// 固定K协同池的独立策略核；不发起I/O，不改变GC/RR选区，不代表已接入ZenFS。
#pragma once
#include <algorithm>
#include <cstdint>
#include <map>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace rrzc_colocation {
// 两个特征维度允许同时成立；普通类别不参与协同放置。
enum class Tag : uint8_t { kNormal=0, kReadHot=1, kShortLived=2, kBoth=3 };
enum class State { kEmpty, kOpen, kClosed, kFull, kUnavailable };
struct Zone {
  uint64_t id=0,generation=0,remaining=0;
  State state=State::kUnavailable;
  bool busy=false,reserved=false,content_compatible=false,risk_safe=false;
};
struct Member {
  uint64_t zone=0,generation=0;
  uint8_t observed_tags=0;
};
struct Resources {
  // 传入ZenFS实际剩余令牌；K从普通Active预算划分，不增加设备名额。
  uint32_t ordinary_active_budget=0,free_active=0,free_open=0;
};
enum class Action { kFallback, kReuse, kCreate };
struct Decision {
  Action action=Action::kFallback;
  uint64_t zone=0,generation=0,revision=0;
  Tag tag=Tag::kNormal;
  std::string reason;
};

class Pool {
 public:
  explicit Pool(uint32_t k=0):k_(k) {}

  // 调用方须提供本池所有Zone的真实状态；缺帧时停止新分配，不能当作已释放。
  Decision Choose(const std::vector<Zone>& zones,const Resources& resources,
                  Tag tag,uint64_t required,const std::set<uint64_t>& sources={}) const {
    std::lock_guard<std::mutex> lock(mutex_);
    Decision result;result.revision=revision_;result.tag=tag;
    auto fallback=[&](const char* why){result.reason=why;return result;};
    if(!k_)return fallback("协同池关闭，使用原分配路径");
    if(k_>resources.ordinary_active_budget)
      throw std::invalid_argument("K超过普通Active预算");
    if(tag==Tag::kNormal)return fallback("缺少合格标签");
    if(static_cast<unsigned>(tag)>3 || !required)
      throw std::invalid_argument("标签或申请容量无效");
    const auto observed=Index(zones);
    uint32_t active=0;
    for(const auto& item:members_) {
      const auto it=observed.find(item.first);
      if(it==observed.end())return fallback("池成员状态缺失，禁止推测名额已释放");
      const auto& zone=*it->second;
      if(zone.state==State::kUnavailable)
        return fallback("池成员暂不可核验，禁止推测名额已释放");
      if(zone.generation!=item.second.generation)
        return fallback("池成员Reset代际已变，须先同步成员状态");
      if(zone.state==State::kOpen || zone.state==State::kClosed ||
         zone.state==State::kEmpty)++active;
    }
    if(!resources.free_open)return fallback("无普通Open令牌，立即回退");
    const Zone* best=nullptr;bool complementary=false;
    for(const auto& item:members_) {
      const auto& zone=*observed.at(item.first);
      if(!Usable(zone,required,sources) || zone.state==State::kEmpty)continue;
      const bool adds=(item.second.observed_tags|static_cast<uint8_t>(tag))!=
                      item.second.observed_tags;
      if(!best || (adds && !complementary) || (adds==complementary && zone.id<best->id)) {
        best=&zone;complementary=adds;
      }
    }
    if(best) {
      result.action=Action::kReuse;result.zone=best->id;result.generation=best->generation;
      result.reason=complementary?"复用现有目标，补充另一特征":"复用现有协同目标";
      return result;
    }
    if(active>=k_)return fallback("池名额已满或目标忙，不等待配对");
    if(!resources.free_active)return fallback("无普通Active令牌，不强制Finish");
    for(const auto& zone:zones)
      if(zone.state==State::kEmpty && !members_.count(zone.id) &&
          Usable(zone,required,sources) && (!best || zone.id<best->id))best=&zone;
    if(!best)return fallback("无安全空目标，回退原分配");
    result.action=Action::kCreate;result.zone=best->id;result.generation=best->generation;
    result.reason="在K及普通资源预算内申请空目标";
    return result;
  }

  // 仅在原生分配器实际取得独占权及令牌后提交；过期建议一律拒绝。
  // 容量、RC、busy和源排除仍须在原生Zone独占下复核。
  bool Commit(const Decision& decision) {
    std::lock_guard<std::mutex> lock(mutex_);
    if(decision.action==Action::kFallback || decision.revision!=revision_ ||
        decision.tag==Tag::kNormal)return false;
    auto found=members_.find(decision.zone);
    if(decision.action==Action::kCreate) {
      if(!k_ || found!=members_.end())return false;
      members_.emplace(decision.zone,Member{decision.zone,decision.generation,
                       static_cast<uint8_t>(decision.tag)});
    } else {
      if(found==members_.end() || found->second.generation!=decision.generation)return false;
      found->second.observed_tags|=static_cast<uint8_t>(decision.tag);
    }
    ++revision_;return true;
  }

  // Reset必须由真实后端事件证明；这里仅清理策略状态，不自行擦除或修改读计数。
  void Reset(uint64_t zone) {
    std::lock_guard<std::mutex> lock(mutex_);
    if(members_.erase(zone))++revision_;
  }

  // 未来缩池只限制新申请；已有成员不Finish、不Reset，等待正常写满或回收。
  void SetLimit(uint32_t k) {
    std::lock_guard<std::mutex> lock(mutex_);k_=k;++revision_;
  }

  bool Contains(uint64_t zone) const {
    std::lock_guard<std::mutex> lock(mutex_);return members_.count(zone)!=0;
  }
  std::vector<Member> Snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);std::vector<Member> out;
    for(const auto& item:members_)out.push_back(item.second);
    return out;
  }

  // 这里只验证调用方提交的恢复证据；持久化、会话身份和文件映射核验由适配层负责。
  // 不得将本接口称为崩溃一致性或完整重挂载验收。
  void Restore(const std::vector<Member>& saved,const std::vector<Zone>& zones) {
    const auto observed=Index(zones);std::map<uint64_t,Member> rebuilt;
    for(const auto& member:saved) {
      if(!member.observed_tags || member.observed_tags>3 || rebuilt.count(member.zone))
        throw std::invalid_argument("恢复成员标签无效或身份重复");
      const auto it=observed.find(member.zone);
      if(it==observed.end())throw std::invalid_argument("恢复缺少真实Zone状态");
      const auto& zone=*it->second;
      if(zone.generation!=member.generation || zone.state==State::kEmpty)continue;
      if(zone.reserved || !zone.content_compatible || zone.state==State::kUnavailable)
        throw std::invalid_argument("恢复成员与预留或内容状态冲突");
      rebuilt.emplace(member.zone,member);
    }
    std::lock_guard<std::mutex> lock(mutex_);members_=std::move(rebuilt);++revision_;
  }

 private:
  static std::map<uint64_t,const Zone*> Index(const std::vector<Zone>& zones) {
    std::map<uint64_t,const Zone*> out;
    for(const auto& zone:zones)
      if(!out.emplace(zone.id,&zone).second)
        throw std::invalid_argument("Zone观测身份重复");
    return out;
  }
  static bool Usable(const Zone& zone,uint64_t required,const std::set<uint64_t>& sources) {
    return (zone.state==State::kEmpty || zone.state==State::kOpen || zone.state==State::kClosed) &&
        !zone.busy && !zone.reserved && zone.content_compatible && zone.risk_safe &&
        zone.remaining>=required && !sources.count(zone.id);
  }
  uint32_t k_;
  uint64_t revision_=0;
  std::map<uint64_t,Member> members_;
  mutable std::mutex mutex_;
};
}  // 固定协同池策略命名空间结束。
