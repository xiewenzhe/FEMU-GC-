// 仅诊断：每请求一条设备读命令累计摘要，阈值反馈稀疏输出，不改变IO返回值。
#pragma once
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <initializer_list>
#include <mutex>
#include <stdexcept>

namespace rrzc_io_trace {
struct Context {
  bool active=false,writer=false;
  uint64_t sequence=0,index=0,key=0;
};
inline thread_local Context context;
class Ledger {
 public:
  Ledger() {
    const char* path=std::getenv("RRZC_CAL_IO_TRACE");
    enabled_=path && *path;
    if(!enabled_)return;
    file_=std::fopen(path,"wbx");
    if(!file_)throw std::runtime_error("设备读摘要文件已存在或不可创建");
    if(std::setvbuf(file_,nullptr,_IOFBF,65536) || std::fwrite("RRZCIO01",1,8,file_)!=8)
      throw std::runtime_error("设备读摘要头写入失败");
  }
  ~Ledger(){if(file_)std::fclose(file_);}
  bool Enabled() const{return enabled_;}
  void Start() {
    if(!enabled_)return;
    std::lock_guard<std::mutex> guard(mutex_);
    if(!started_) {Record(UINT64_MAX);started_=true;}
  }
  void Read(uint64_t lba,bool migration,int status,uint32_t result) {
    if(!enabled_)return;
    std::lock_guard<std::mutex> guard(mutex_);
    ++commands_;
    for(uint64_t value:{lba,uint64_t(migration),uint64_t(int64_t(status)),uint64_t(result)}) {
      hash_=(hash_^value)*1099511628211ULL;
    }
    if(status || result) {
      const auto now=std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now().time_since_epoch()).count();
      std::fprintf(stderr,"严格RR命令反馈：客机纳秒=%llu，前台上下文=%d，请求序号=%llu，写角色=%d，角色序号=%llu，Key=%llu，LBA=%llu，搬迁=%d，状态=%d，结果=%u，累计读命令=%llu，累计指纹=%llu\n",
          (unsigned long long)now,context.active,(unsigned long long)context.sequence,
          context.writer,(unsigned long long)context.index,(unsigned long long)context.key,
          (unsigned long long)lba,migration,status,result,(unsigned long long)commands_,
          (unsigned long long)hash_);
    }
  }
  void End(uint64_t sequence) {
    if(!enabled_)return;
    std::lock_guard<std::mutex> guard(mutex_);Record(sequence);
  }
  void Finish() {
    if(!enabled_)return;
    std::lock_guard<std::mutex> guard(mutex_);
    if(file_) {
      FILE* file=file_;file_=nullptr;
      if(std::fclose(file))throw std::runtime_error("设备读摘要落盘失败");
    }
  }
 private:
  void Record(uint64_t sequence) {
    if(!file_)throw std::runtime_error("设备读摘要意外提前关闭");
    unsigned char bytes[24];size_t offset=0;
    for(uint64_t value:{sequence,commands_,hash_})for(unsigned n=0;n<8;++n)
      bytes[offset++]=static_cast<unsigned char>(value>>(8*n));
    if(std::fwrite(bytes,1,sizeof(bytes),file_)!=sizeof(bytes))
      throw std::runtime_error("设备读摘要写入失败");
  }
  bool enabled_=false,started_=false;
  FILE* file_=nullptr;
  uint64_t commands_=0,hash_=1469598103934665603ULL;
  std::mutex mutex_;
};
inline Ledger& State(){static Ledger state;return state;}
inline void Begin(uint64_t sequence,bool writer,uint64_t index,uint64_t key) {
  if(!State().Enabled())return;
  if(context.active)throw std::runtime_error("设备读摘要请求上下文重入");
  context={true,writer,sequence,index,key};State().Start();
}
inline void End(uint64_t sequence) {
  if(!State().Enabled())return;
  if(!context.active || context.sequence!=sequence)throw std::runtime_error("设备读摘要请求序号不符");
  State().End(sequence);context={};
}
inline void Read(uint64_t lba,bool migration,int status,uint32_t result) {
  State().Read(lba,migration,status,result);
}
inline void Finish(){State().Finish();}
}
