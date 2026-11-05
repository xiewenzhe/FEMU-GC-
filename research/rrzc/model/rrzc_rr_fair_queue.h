// RR反饥饿队列：只排序已触发RR的区，不改变阈值、周期、迁移资格或ZC模型。
#pragma once
#include <cstdint>
#include <map>

namespace rrzc_rr_fair {
struct Pending {
  uint64_t generation=0,sequence=0,first_ns=0;
};
class Queue {
 public:
  void Observe(uint64_t zone,bool known,uint64_t generation,uint64_t sequence,
               bool active,uint64_t now) {
    // 不可信观察不能充当已维护证据；保留旧等待，后续稳定代际再决定。
    if (!known) return;
    if (!active) { pending_.erase(zone);return; }
    const auto it=pending_.find(zone);
    if (it==pending_.end() || it->second.generation!=generation || it->second.sequence!=sequence)
      pending_[zone]={generation,sequence,now};
  }
  uint64_t First(uint64_t zone) const {
    const auto it=pending_.find(zone);
    return it==pending_.end() ? UINT64_MAX : it->second.first_ns;
  }
  bool Before(uint64_t a,uint64_t b) const {
    const auto x=First(a),y=First(b);
    return x!=y ? x<y : a<b;
  }
 private:
  // 每个ZenFS实例独享，仅原维护线程访问；不能用静态全局跨挂载拼接。
  std::map<uint64_t,Pending> pending_;
};
}  // RR公平队列命名空间结束。
