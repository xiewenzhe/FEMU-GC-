// 普通分配按票号先来先服务；GC迁移不进入队列，仍可回收释放空间。
#pragma once
#include <condition_variable>
#include <cstdint>
#include <mutex>
namespace rrzc_fifo {
class Queue {
  std::mutex mutex_;
  std::condition_variable changed_;
  uint64_t next_=0,serving_=0;
 public:
  uint64_t Enter() {
    std::unique_lock<std::mutex> lock(mutex_);
    const auto ticket=next_++;
    changed_.wait(lock,[&]{return serving_==ticket;});
    return ticket;
  }
  void Leave() {
    { std::lock_guard<std::mutex> lock(mutex_); ++serving_; }
    changed_.notify_all();
  }
  uint64_t Issued() { std::lock_guard<std::mutex> lock(mutex_);return next_; }
  class Turn {
    Queue& queue_;
    uint64_t number_;
   public:
    explicit Turn(Queue& queue):queue_(queue),number_(queue.Enter()){}
    ~Turn(){queue_.Leave();}
    uint64_t Number()const{return number_;}
    Turn(const Turn&)=delete;
    Turn& operator=(const Turn&)=delete;
  };
};
}  // 命名空间结束。
