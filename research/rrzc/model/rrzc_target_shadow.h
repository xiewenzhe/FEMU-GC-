// 同次目标比较只用于观测；不持有Zone、不申请令牌、不修改放置或维护。
#pragma once
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <set>
#include <sstream>
#include <vector>

namespace rrzc_target_shadow {
inline bool Enabled() noexcept {
  static const bool enabled=[] {
    const char* value=std::getenv("ZENFS_RRZC_TARGET_SHADOW");
    if(!value || !std::strcmp(value,"0"))return false;
    if(!std::strcmp(value,"1"))return true;
    std::fprintf(stderr,"目标备选影子配置无效，观测已关闭；原分配继续\n");
    return false;
  }();
  return enabled;
}
inline uint64_t Now() noexcept {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
}
struct Row {
  uint64_t zone=0,generation=0,remaining=0,used=0,rc=0,threshold=0;
  unsigned difference=0;
  bool known=false,full=false,pool=false,compatible=false,rc_known=false;
};
struct Advice {
  bool has_partial=false,complete=false;
  uint64_t unknown=0;
  Row partial;
};
// 严格保留原普通扫描的输入顺序和<=同分替换；不追加原算法没有的RC/源过滤。
inline Advice Choose(const std::vector<Row>& rows,uint64_t minimum,
                     unsigned initial_difference,bool captured,bool valid) {
  Advice out;out.complete=captured && valid;
  unsigned best=initial_difference;
  for(const auto& row:rows) {
    if(!row.known){++out.unknown;out.complete=false;continue;}
    if(row.used && !row.full && !row.pool && row.compatible &&
       row.remaining>=minimum && row.difference<=best) {
      out.has_partial=true;out.partial=row;best=row.difference;
    }
  }
  return out;
}
class Scope;
inline thread_local Scope* current=nullptr;
inline std::atomic<uint64_t> next_id{0};
class Scope {
 public:
  Scope(bool active,unsigned lifetime,uint64_t minimum):previous_(current),
      active_(active),lifetime_(lifetime),minimum_(minimum) {
    current=active?this:nullptr;
    if(active){id_=next_id.fetch_add(1)+1;begin_=Now();}
  }
  ~Scope(){current=previous_;}
  Scope(const Scope&)=delete;
  Scope& operator=(const Scope&)=delete;
  bool Active() const noexcept {return active_;}
  unsigned Lifetime() const noexcept {return lifetime_;}
  template<class Members> void Begin(Members members) noexcept {
    try {
      captured_=true;rows_.clear();members_.clear();
      for(const auto& item:members())members_.insert(item.zone);
    }catch(...){valid_=false;}
  }
  bool Member(uint64_t zone) const {return members_.count(zone)!=0;}
  template<class Sample> void Capture(Sample sample) noexcept {
    if(!valid_)return;
    try {rows_.push_back(sample());}catch(...){valid_=false;}
  }
  void Unknown(uint64_t zone) noexcept {
    Capture([&]{Row row;row.zone=zone;return row;});
  }
  void Invalidate() noexcept {valid_=false;}
  std::string Describe(uint64_t owner,uint8_t tag,bool append,unsigned action,
      uint64_t pool_zone,uint64_t pool_generation,unsigned initial_difference,
      uint64_t free_active,uint64_t free_open) const {
    const auto advice=Choose(rows_,minimum_,initial_difference,captured_,valid_);
    const Row* cooperative=nullptr;
    if(action)for(const auto& row:rows_)
      if(row.known && row.zone==pool_zone && row.generation==pool_generation)cooperative=&row;
    std::ostringstream out;
    out<<"决策="<<id_<<"，对象="<<owner<<"，标签="<<unsigned(tag)
       <<"，路径="<<(append?"新写":"必要迁移")<<"，普通容量下限="<<minimum_
       <<"，开始纳秒="<<begin_<<"，完成纳秒="<<Now()
       <<"，观察完整="<<advice.complete<<"，未观察区数="<<advice.unknown
       <<"，空闲Active="<<free_active<<"，空闲Open="<<free_open
       <<"，协同建议动作="<<action<<"，协同建议Zone="<<pool_zone
       <<"，协同代际="<<pool_generation<<"，协同字段可知="<<(cooperative!=nullptr);
    auto fields=[&](const char* prefix,const Row& r) {
      out<<"，"<<prefix<<"剩余="<<r.remaining<<"，"<<prefix<<"有效="<<r.used
         <<"，"<<prefix<<"RC可知="<<r.rc_known<<"，"<<prefix<<"RC="<<r.rc
         <<"，"<<prefix<<"阈值="<<r.threshold;
    };
    if(cooperative)fields("协同",*cooperative);
    out<<"，普通部分区扫描建议="<<advice.has_partial;
    if(advice.has_partial) {
      out<<"，普通建议Zone="<<advice.partial.zone<<"，普通代际="<<advice.partial.generation
         <<"，普通寿命差="<<advice.partial.difference;
      fields("普通",advice.partial);
    }
    out<<"，普通实际最终目标=未求解，普通预留或空区后续=未执行未知"
       <<"，普通RC不参与原排名=1，目标未独占=1，未申请令牌=1，未提交=1";
    return out.str();
  }
 private:
  Scope* previous_;
  bool active_,captured_=false,valid_=true;
  unsigned lifetime_;
  uint64_t minimum_,id_=0,begin_=0;
  std::set<uint64_t> members_;
  std::vector<Row> rows_;
};
}  // 观察建议不进入真实目标决策。
