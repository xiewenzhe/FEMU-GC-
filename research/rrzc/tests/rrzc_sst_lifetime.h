// 真实SST生命周期筛查计划；只定义逻辑请求，不读取维护结果来调整负载。
#pragma once
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <stdexcept>

namespace rrzc_sst_lifetime {
struct Plan {
  bool smoke;
  uint64_t total,windows,width,hot,chunk,read_batch;
  bool persistent_h;
  static Plan Make(bool smoke,bool persistent_h=false) {
    return smoke?Plan{true,1024,1,256,16,32,32,persistent_h}:
                 Plan{false,917504,6,65536,4096,2048,8192,persistent_h};
  }
  uint64_t Writes() const{return windows*2*(width-hot);}
  uint64_t Reads() const{return windows*8*read_batch;}
  uint64_t WriteKey(uint64_t window,uint64_t index) const {
    if(window>=windows || index>=width-hot)throw std::runtime_error("W覆盖键超出预注册范围");
    return window*width+hot+index;
  }
  uint64_t ReadKey(uint64_t window,uint64_t index) const {
    if(window>=windows)throw std::runtime_error("H读取窗口越界");
    // 固定热点只改变H键范围；W覆盖、次数及检查时点不变。
    return (persistent_h?0:window*width)+index%hot;
  }
};
// 与既有审计器同格式的成功请求记录；本入口只有一个前台线程。
class Trace {
 public:
  explicit Trace(const char* path) {
    if(!path || !*path)throw std::runtime_error("生命周期实验必须记录逐请求输入");
    stream_=std::fopen(path,"wbx");
    if(!stream_)throw std::runtime_error("逐请求文件已存在或不可创建");
    if(std::setvbuf(stream_,nullptr,_IOFBF,65536)!=0 || std::fwrite("RRZCREQ1",1,8,stream_)!=8)
      throw std::runtime_error("逐请求头写入失败");
  }
  ~Trace(){if(stream_)std::fclose(stream_);}
  void Record(bool write,uint64_t key,uint64_t start,uint64_t api,uint64_t release) {
    if(start<last_release_ || start>api || api>release)throw std::runtime_error("单线程请求时间倒退");
    unsigned char bytes[56];unsigned offset=0;const unsigned role=write?1:0;
    for(uint64_t n:{sequence_++,uint64_t(role),role_index_[role]++,key,start,api,release})
      for(unsigned i=0;i<8;++i)bytes[offset++]=static_cast<unsigned char>(n>>(8*i));
    if(std::fwrite(bytes,1,56,stream_)!=56)throw std::runtime_error("逐请求记录失败");
    last_release_=release;
  }
  uint64_t Count() const{return sequence_;}
  void Finish(){auto* f=stream_;stream_=nullptr;if(std::fclose(f))throw std::runtime_error("请求文件关闭失败");}
 private:
  FILE* stream_=nullptr;uint64_t sequence_=0,role_index_[2]={0,0},last_release_=0;
};
}  // 预先固定W覆盖及H读取，不设置QPS限制。
