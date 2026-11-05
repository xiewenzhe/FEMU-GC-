#pragma once
// 应用请求队列与逐笔完成记录；写完成绑定真实同步Flush，不摊分批次耗时。
#include "rrzc_sst_lifetime.h"
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <exception>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>

namespace rrzc_end_to_end {
inline uint64_t Now(){return std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count();}
struct Request {uint64_t seq=0,write=0,key=0,created=0,enqueued=0,dispatched=0,begin=0,end=0;};
class Queue {
  const rrzc_sst_lifetime::Plan plan_;
  const size_t capacity_;
  std::mutex mutex_;std::condition_variable ready_,space_;
  std::deque<Request> queue_;bool done_=false,cancel_=false;std::exception_ptr error_;
  std::thread producer_;
  void Produce(){
    try{
      uint64_t seq=0;
      auto submit=[&](bool write,uint64_t key){
        Request r;r.seq=seq++;r.write=write;r.key=key;r.created=Now();
        std::unique_lock<std::mutex> lock(mutex_);
        space_.wait(lock,[&]{return cancel_ || queue_.size()<capacity_;});
        if(cancel_)return false;
        r.enqueued=Now();queue_.push_back(r);ready_.notify_one();return true;
      };
      for(uint64_t w=0;w<plan_.windows;++w)for(uint64_t pass=0;pass<2;++pass){
        uint64_t reads=0;
        for(uint64_t n=0;n<plan_.width-plan_.hot;++n){
          if(!submit(true,plan_.WriteKey(w,n)))return;
          const auto due=(n+1)*(4*plan_.read_batch)/(plan_.width-plan_.hot);
          while(reads<due)if(!submit(false,plan_.ReadKey(w,pass*4*plan_.read_batch+reads++)))return;
        }
      }
    }catch(...){std::lock_guard<std::mutex> lock(mutex_);error_=std::current_exception();}
    {std::lock_guard<std::mutex> lock(mutex_);done_=true;}ready_.notify_all();
  }
 public:
  Queue(const rrzc_sst_lifetime::Plan& plan,size_t capacity):plan_(plan),capacity_(capacity){
    if(!capacity_)throw std::runtime_error("应用队列容量必须大于零");
    producer_=std::thread([this]{Produce();});
  }
  ~Queue(){
    {std::lock_guard<std::mutex> lock(mutex_);cancel_=true;}
    space_.notify_all();ready_.notify_all();if(producer_.joinable())producer_.join();
  }
  Request Take(uint64_t seq,bool write,uint64_t key){
    std::unique_lock<std::mutex> lock(mutex_);
    ready_.wait(lock,[&]{return done_ || !queue_.empty();});
    if(error_)std::rethrow_exception(error_);
    if(queue_.empty())throw std::runtime_error("应用请求队列提前结束");
    auto r=queue_.front();r.dispatched=Now();queue_.pop_front();space_.notify_one();
    if(r.seq!=seq || r.write!=uint64_t(write) || r.key!=key)
      throw std::runtime_error("应用生成流与固定提交流不一致");
    return r;
  }
};

class Ledger {
  FILE* file_=nullptr;
  std::vector<Request> pending_;
  uint64_t completed_=0,batch_=0;
  void Row(const Request& r,uint64_t complete,uint64_t batch){
    if(!(r.created<=r.enqueued && r.enqueued<=r.dispatched && r.dispatched<=r.begin &&
         r.begin<=r.end && r.end<=complete))throw std::runtime_error("应用完成时间线倒退");
    unsigned char bytes[80];size_t i=0;
    for(uint64_t value:{r.seq,r.write,r.key,r.created,r.enqueued,r.dispatched,r.begin,r.end,complete,batch})
      for(unsigned b=0;b<8;++b)bytes[i++]=static_cast<unsigned char>(value>>(8*b));
    if(fwrite(bytes,1,sizeof(bytes),file_)!=sizeof(bytes))throw std::runtime_error("应用完成记录写出失败");
    ++completed_;
  }
 public:
  explicit Ledger(uint64_t capacity,uint64_t batch_size){
    const char* path=getenv("RRZC_END_TO_END");
    if(!path || !*path)throw std::runtime_error("缺少应用完成记录路径");
    file_=fopen(path,"wbx");if(!file_)throw std::runtime_error("应用完成文件不可创建");
    if(setvbuf(file_,nullptr,_IOFBF,65536) || fwrite("RRZCE201",1,8,file_)!=8)
      throw std::runtime_error("应用完成头写入失败");
    unsigned char bytes[16];size_t i=0;
    for(uint64_t n:{capacity,batch_size})for(unsigned b=0;b<8;++b)bytes[i++]=static_cast<unsigned char>(n>>(8*b));
    if(fwrite(bytes,1,16,file_)!=16)throw std::runtime_error("应用完成参数写入失败");
    pending_.reserve(batch_size);
  }
  ~Ledger(){if(file_)fclose(file_);}
  void Api(Request r,uint64_t begin,uint64_t end){
    r.begin=begin;r.end=end;
    if(r.write)pending_.push_back(r);else Row(r,Now(),0);
  }
  void Flushed(uint64_t begin,uint64_t end){
    if(pending_.empty())return;
    ++batch_;const uint64_t completed=Now();
    fprintf(stderr,"应用写批次完成：批次=%llu，写数=%zu，首请求=%llu，末请求=%llu，Flush开始纳秒=%llu，Flush返回纳秒=%llu，完成纳秒=%llu\n",
      (unsigned long long)batch_,pending_.size(),(unsigned long long)pending_.front().seq,
      (unsigned long long)pending_.back().seq,(unsigned long long)begin,(unsigned long long)end,
      (unsigned long long)completed);
    for(const auto& r:pending_)Row(r,completed,batch_);
    pending_.clear();
  }
  void Finish(uint64_t expected){
    if(!pending_.empty() || completed_!=expected)throw std::runtime_error("应用完成数或持久化批次未闭合");
    auto* file=file_;file_=nullptr;if(fclose(file))throw std::runtime_error("应用完成证据关闭失败");
  }
};
}
