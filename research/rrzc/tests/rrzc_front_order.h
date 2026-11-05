// 受控实验的前台全局顺序：每组4读3写；不持锁执行IO，不设置时间或QPS上限。
#pragma once
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <chrono>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>

namespace rrzc_front_order {
struct Checkpoint { uint64_t operations,hash; };
class Order {
 public:
  Order() {
    const char* path=std::getenv("RRZC_CAL_REQUEST_TRACE");
    if(path && *path) {
      trace_=std::fopen(path,"wbx");
      if(!trace_)throw std::runtime_error("逐请求跟踪文件已存在或不可创建");
      if(std::setvbuf(trace_,nullptr,_IOFBF,65536)!=0 ||
         std::fwrite("RRZCREQ1",1,8,trace_)!=8) {
        std::fclose(trace_);trace_=nullptr;
        throw std::runtime_error("逐请求跟踪头写入失败");
      }
    }
  }
  ~Order() {if(trace_)std::fclose(trace_);}
  void Before(bool writer,uint64_t index,uint64_t key) {
    std::unique_lock<std::mutex> lock(mutex_);
    changed_.wait(lock,[&]{return writer_turn_==writer;});
    auto& next=writer?next_write_:next_read_;
    if(busy_ || index!=next)throw std::runtime_error("受控请求顺序或并发状态异常");
    ++next;busy_=true;owner_=writer;
    hash_=(hash_^key^(writer?0x57ULL:0x52ULL))*1099511628211ULL;++operations_;
    if(trace_) {trace_index_=index;trace_key_=key;trace_begin_=Now();trace_api_=0;}
  }
  // 成功API返回后、等待后台之前记录；不把排空等待混入API耗时。
  void ApiCompleted() {
    if(!trace_)return;
    std::lock_guard<std::mutex> lock(mutex_);
    if(!busy_ || trace_api_)throw std::runtime_error("请求完成跟踪重复或缺少提交");
    trace_api_=Now();
  }
  void After(bool writer,uint64_t index) {
    std::lock_guard<std::mutex> lock(mutex_);
    if(!busy_ || owner_!=writer)throw std::runtime_error("受控请求完成没有对应开始");
    if(trace_) {
      if(!trace_api_)throw std::runtime_error("逐请求跟踪缺少成功API完成");
      // 固定小端七列：全局序号、写标志、角色内序号、键、提交、API完成、放行。
      unsigned char bytes[56];size_t offset=0;
      for(uint64_t value:{operations_-1,uint64_t(writer),trace_index_,trace_key_,trace_begin_,trace_api_,Now()})
        for(unsigned i=0;i<8;++i)bytes[offset++]=static_cast<unsigned char>(value>>(8*i));
      if(std::fwrite(bytes,1,sizeof(bytes),trace_)!=sizeof(bytes))
        throw std::runtime_error("逐请求跟踪写入失败");
    }
    busy_=false;
    if((index+1)%(writer?3:4)==0){writer_turn_=!writer;changed_.notify_all();}
  }
  Checkpoint Snapshot() {
    std::lock_guard<std::mutex> lock(mutex_);
    if(busy_)throw std::runtime_error("请求未完成时不能记录受控检查点");
    return {operations_,hash_};
  }
  void FinishTrace() {
    std::lock_guard<std::mutex> lock(mutex_);
    if(busy_)throw std::runtime_error("请求未放行时不能关闭跟踪");
    if(trace_) {
      FILE* file=trace_;trace_=nullptr;
      if(std::fclose(file)!=0)throw std::runtime_error("逐请求跟踪落盘失败");
    }
  }
 private:
  static uint64_t Now() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
  }
  FILE* trace_=nullptr;
  uint64_t trace_index_=0,trace_key_=0,trace_begin_=0,trace_api_=0;
  std::mutex mutex_;std::condition_variable changed_;
  bool writer_turn_=false,busy_=false,owner_=false;
  uint64_t next_read_=0,next_write_=0,operations_=0,hash_=1469598103934665603ULL;
};
inline Order& State(){static Order state;return state;}

// 只在受控实验启用：一批前台请求结束后，要求后台完成恰好一次原有GC资格检查。
inline void GcTick(const char* base,uint64_t number) {
  if(!base || !*base)return;
  const std::string path(base),temporary=path+".request.tmp."+std::to_string(getpid());
  FILE* file=fopen(temporary.c_str(),"w");
  if(!file || fprintf(file,"%llu\n",(unsigned long long)number)<0 || fclose(file)!=0 ||
     rename(temporary.c_str(),(path+".request").c_str())!=0)
    throw std::runtime_error("受控GC请求写入失败");
  const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(120);
  while(std::chrono::steady_clock::now()<deadline) {
    FILE* ack=fopen((path+".ack").c_str(),"r");
    if(ack) {
      unsigned long long current=0;
      const bool valid=fscanf(ack,"%llu",&current)==1;
      fclose(ack);
      if(valid && current==number)return;
      if(valid && current>number)throw std::runtime_error("受控GC确认序号越过请求");
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  throw std::runtime_error("受控GC检查点120秒未确认");
}
}
