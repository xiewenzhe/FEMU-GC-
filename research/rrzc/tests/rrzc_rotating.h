// 独立定制db_bench：固定操作批次和热点切换，不依赖RR/GC结果，不限速。
#pragma once
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <mutex>
#include <vector>
#include "tools/rrzc_dbbench_hotspot.h"

namespace rrzc_rotating {
using Clock=std::chrono::steady_clock;
inline uint64_t Now() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
}
struct Plan {
  uint64_t rounds,read_batch,write_batch,phase_batches;
  static Plan Load(uint64_t reads,uint64_t writes,uint64_t total) {
    const auto rounds=rrzc_dbbench_hotspot::Number("RRZC_ROTATE_ROUNDS",24);
    const auto phase=rrzc_dbbench_hotspot::Number("RRZC_ROTATE_PHASE_BATCHES",4);
    if(!rounds || !phase || rounds>128 || !reads || !writes || reads%rounds || writes%rounds ||
        reads>4000000 || writes>4000000 || total<65536)
      throw std::runtime_error("轮转批次参数非法，不自动改变负载");
    return {rounds,reads/rounds,writes/rounds,phase};
  }
  uint64_t Base(uint64_t round,uint64_t total) const {
    return ((round/phase_batches)%(total/65536))*65536;
  }
  uint64_t Key(bool writer,uint64_t random,uint64_t round,uint64_t total) const {
    const auto first=Base(round,total);
    if(!writer)return first+random%4096;
    const bool local=random%100<80;random/=100;
    if(local)return first+4096+random%61440;
    // 其余写入排除当前读窗口，允许覆盖已降温的旧窗口。
    auto key=random%(total-4096);
    return key<first?key:key+4096;
  }
};
class Barrier {
 public:
  void Wait() {
    std::unique_lock<std::mutex> lock(mutex_);const auto old=generation_;
    if(++arrived_==2){arrived_=0;++generation_;changed_.notify_all();}
    else changed_.wait(lock,[&]{return generation_!=old;});
  }
 private:
  std::mutex mutex_;std::condition_variable changed_;unsigned arrived_=0,generation_=0;
};
inline Barrier& Sync() {static Barrier barrier;return barrier;}
inline void Latency(const char* role,std::vector<uint64_t>& values) {
  if(values.empty())throw std::runtime_error("缺少真实API耗时");
  long double sum=0;for(auto n:values)sum+=n;
  std::sort(values.begin(),values.end());
  auto q=[&](uint64_t units){return values[(values.size()*units+999999)/1000000-1];};
  fprintf(stderr,"RRZC调用延迟：操作=%s，次数=%zu，平均纳秒=%.6Lf，P99纳秒=%llu，P99.9纳秒=%llu，P99.99纳秒=%llu\n",
      role,values.size(),sum/values.size(),(unsigned long long)q(990000),
      (unsigned long long)q(999000),(unsigned long long)q(999900));
}
}  // 定制轮转负载辅助模块结束。
