// 只记录状态变化；内存事件不返回任何策略分数、目标或资源建议。
#pragma once
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fcntl.h>
#include <initializer_list>
#include <mutex>
#include <thread>
#include <unistd.h>
#include <vector>

namespace rrzc_placement_trace {
enum Kind : uint64_t {
  kZone=1,kOwner=2,kAdd=3,kClear=4,kMapBegin=5,kMapEnd=6,kRemove=7,
  kHeat=8,kAttempt=9,kG1Skip=10,kPoolTarget=11,kTarget=12,
  kCopy=13,kTransactionEnd=14,kReset=15,kRetire=16,kPendingRef=17,
  kUncertain=18,kOwnerEnd=19,kProfile=20,kClock=21,kMode=22,
  kVerifyBegin=23,kVerifyExtent=24,kVerifyEnd=25,kVerifySkip=26,kBoundary=27,
  kZoneRole=28,kMetadataWrite=29
};
struct Event { std::array<uint64_t,16> value{}; };
static_assert(sizeof(Event)==128,"事件格式应为16个64位字段");
class Sink {
 public:
  Sink() {
    const char* path=std::getenv("ZENFS_RRZC_PLACEMENT_TRACE");
    if(!path || !*path)return;
    fd_=::open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    if(fd_<0){std::fprintf(stderr,"放置观测文件创建失败，未启用观测\n");return;}
    const std::array<uint64_t,3> header{0,0,0};
    if(!Write("RRZCCP01",8) || !Write(header.data(),sizeof(header)))failed_=true;
  }
  ~Sink() {Seal();}
  bool AcquireDevice() {
    std::lock_guard<std::mutex> lock(mutex_);
    if(!Enabled())return false;
    ++devices_;return true;
  }
  void ReleaseDevice() {
    bool finish=false;
    {std::lock_guard<std::mutex> lock(mutex_);if(devices_)--devices_;finish=requested_ && devices_==0;}
    if(finish)Seal();
  }
  void RequestSeal() {
    bool finish=false;
    {std::lock_guard<std::mutex> lock(mutex_);requested_=true;finish=devices_==0;}
    if(finish)Seal();
  }
  // 封存只关闭观测窗口。迟于封存的全局文件清理不再写事件，也不访问已释放向量。
  void Seal() {
    std::lock_guard<std::mutex> lock(mutex_);
    if(sealed_.exchange(true) || fd_<0)return;
    if(!failed_ && !events_.empty())Write(events_.data(),events_.size()*sizeof(Event));
    const std::array<uint64_t,3> header{events_.size(),dropped_,failed_?0ULL:1ULL};
    if(::pwrite(fd_,header.data(),sizeof(header),8)!=static_cast<ssize_t>(sizeof(header)))failed_=true;
    if(::fsync(fd_)!=0){
      failed_=true;
      const uint64_t incomplete=0;
      // 同步失败时尽力撤销完成标志，不能把失败文件包装为可靠归档。
      const auto revoked=::pwrite(fd_,&incomplete,sizeof(incomplete),24);
      if(revoked!=static_cast<ssize_t>(sizeof(incomplete)))
        std::fprintf(stderr,"观测完成标志撤销失败，必须结合进程退出和归档校验判定\n");
    }
    ::close(fd_);
    std::vector<Event>().swap(events_);
    if(failed_ || dropped_)std::fprintf(stderr,"放置观测不完整：写失败=%d，丢弃=%lu；不得发布账目通过结论\n",failed_.load(),dropped_);
  }
  bool Enabled() const {return !sealed_ && fd_>=0 && !failed_;}
  void Invalidate() noexcept {failed_=true;}
  uint64_t Id() {return Enabled()?ids_.fetch_add(1)+1:0;}
  void Emit(Kind kind,uint64_t device,uint64_t owner,uint64_t clock,
            std::initializer_list<uint64_t> args={}) noexcept {
    if(!Enabled())return;
    try {
      std::lock_guard<std::mutex> lock(mutex_);
      if(sealed_ || failed_)return;
      // 固定128MiB事件上限；超限显式失败，绝不把截断证据当完整账目。
      if(events_.size()>=(1U<<20) || args.size()>9){++dropped_;return;}
      Event e;e.value[0]=events_.size()+1;
      e.value[1]=std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now().time_since_epoch()).count();
      e.value[2]=std::hash<std::thread::id>{}(std::this_thread::get_id());
      e.value[3]=kind;e.value[4]=device;e.value[5]=owner;e.value[6]=clock;
      size_t i=7;for(auto x:args)e.value[i++]=x;
      events_.push_back(e);
    }catch(...){failed_=true;}
  }
 private:
  bool Write(const void* data,size_t size) {
    const auto* bytes=static_cast<const char*>(data);
    while(size){const auto n=::write(fd_,bytes,size);if(n<=0){failed_=true;return false;}bytes+=n;size-=n;}
    return true;
  }
  int fd_=-1;
  std::atomic<bool> failed_{false},sealed_{false};
  std::atomic<uint64_t> ids_{0};
  uint64_t dropped_=0;
  uint64_t devices_=0;
  bool requested_=false;
  std::mutex mutex_;
  std::vector<Event> events_;
};
inline Sink& Trace(){
  // 原Env可能先于本单例构造、晚于它清理。保留轻量对象至进程结束，显式封存并释放大缓冲。
  static Sink* sink=[] {
    auto* value=new Sink;
    if(std::atexit([] {Trace().RequestSeal();})!=0)value->Invalidate();
    return value;
  }();
  return *sink;
}
inline bool Enabled(){return Trace().Enabled();}
inline uint64_t NewId(){return Trace().Id();}
inline void Invalidate() noexcept {Trace().Invalidate();}
// 只在设备对象构造/析构时登记；没有逐I/O令牌或额外热路径同步。
class DeviceLease {
 public:
  DeviceLease():held_(Trace().AcquireDevice()){}
  ~DeviceLease(){if(held_)Trace().ReleaseDevice();}
  DeviceLease(const DeviceLease&)=delete;
  DeviceLease& operator=(const DeviceLease&)=delete;
 private:
  bool held_;
};
inline void Emit(Kind kind,uint64_t device,uint64_t owner,uint64_t clock,
                 std::initializer_list<uint64_t> args={}) noexcept {
  Trace().Emit(kind,device,owner,clock,args);
}

// 缓存“无后续读取时何时变冷”；复用原Profile互斥区，不遍历其他文件。
// 只有真假或预计过期边界改变时产生事件；原分类计数及返回结果不改。
class HeatProjection {
 public:
  HeatProjection():id_(NewId()) {
    if(id_)Emit(kProfile,0,id_,0);
  }
  uint64_t Id() const {return id_;}
  template<class Stats,class Buckets>
  void Observe(uint64_t event,uint64_t file_bytes,const Stats& current,
               const Buckets& buckets,bool reused,uint64_t bytes,bool mutation=true) {
    if(!id_)return;
    constexpr uint64_t count=64,stride=1024;
    const uint64_t epoch=event/stride;
    const bool stale=!hot_ || epoch>=until_ || size_!=file_bytes;
    const auto previous_until=until_;const bool previous_hot=hot_;
    const bool size_changed=size_!=file_bytes;
    if(!current.hot){hot_=false;until_=0;}
    else {
      if(mutation && !stale && until_-epoch<count){++future_reads_;future_reused_+=reused;future_bytes_+=bytes;}
      if(stale || Hot(future_reads_,future_reused_,future_bytes_,file_bytes)) {
        uint64_t reads=current.reads,repeat=current.reused,total=current.bytes;
        until_=epoch+count;
        for(uint64_t step=1;step<=count;++step) {
          const uint64_t next=epoch+step;
          if(next>=count) {
            const auto& b=buckets[(next-count)%count];
            if(b.epoch==next-count){reads-=b.reads;repeat-=b.reused;total-=b.bytes;}
          }
          if(!Hot(reads,repeat,total,file_bytes)) {
            until_=next;future_reads_=reads;future_reused_=repeat;future_bytes_=total;break;
          }
        }
      }
      hot_=true;
    }
    size_=file_bytes;
    if(previous_hot!=hot_ || previous_until!=until_ ||
        (current.hot && (epoch>=previous_until || size_changed)))
      Emit(kHeat,0,id_,event,{hot_,hot_?until_*stride:0,current.reads,current.reused,current.bytes,file_bytes});
  }
 private:
  static bool Hot(uint64_t reads,uint64_t reused,uint64_t bytes,uint64_t size) {
    return (reads>=256 && reused>=128) || (size && reads>=128 && bytes/size>=4);
  }
  uint64_t id_=0,until_=0,size_=0,future_reads_=0,future_reused_=0,future_bytes_=0;
  bool hot_=false;
};
}  // 只读观测命名空间结束。
