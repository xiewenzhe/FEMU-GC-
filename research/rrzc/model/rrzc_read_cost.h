#ifndef RRZC_READ_COST_H
#define RRZC_READ_COST_H
// 默认关闭的被动观测；不改变锁、请求大小、RR检查或返回值。
#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <mutex>
#include <vector>
#include <sys/syscall.h>
#include <unistd.h>
namespace rrzc_read_cost {
inline uint64_t Now(){return std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count();}
inline bool Enabled(){static bool yes=[](){const char* p=getenv("RRZC_READ_COST");return p && *p;}();return yes;}
inline uint64_t Tid(){static thread_local uint64_t id=static_cast<uint64_t>(syscall(SYS_gettid));return id;}
struct Row {std::array<uint64_t,10> value;};
struct Count {std::atomic<uint64_t> calls{0},ns{0},maximum{0};};
struct Buffer {
  FILE* file=nullptr;std::mutex mutex;std::vector<Row> rows;uint64_t dropped=0;
  std::array<Count,4> costs;
  Buffer(){if(Enabled()){
    file=fopen(getenv("RRZC_READ_COST"),"wbx");
    if(!file){perror("无法创建读成本证据");abort();}rows.reserve(1u<<20);
  }}
  void Add(const Row& row){std::lock_guard<std::mutex> lock(mutex);
    if(rows.size()<(1u<<20))rows.push_back(row);else ++dropped;}
  ~Buffer(){if(!file)return;
    bool ok=fwrite("RRZCRD01",1,8,file)==8;
    ok=(fwrite(&dropped,8,1,file)==1)&&ok;
    for(size_t i=0;i<costs.size();++i){const auto& c=costs[i];
      Row r{{4,i,c.calls.load(),c.ns.load(),c.maximum.load(),0,0,0,0,0}};
      ok=(fwrite(&r,sizeof(r),1,file)==1)&&ok;}
    ok=(fwrite(rows.data(),sizeof(Row),rows.size(),file)==rows.size())&&ok;
    ok=(fclose(file)==0)&&ok;
    if(!ok || dropped)fprintf(stderr,"读成本证据不完整，丢弃=%llu\n",(unsigned long long)dropped);
  }
};
inline Buffer& State(){static Buffer buffer;return buffer;}
struct Front {bool active=false;uint64_t seq=0,key=0,wait=0,body=0,io=0,commands=0;};
inline thread_local Front front;
inline thread_local uint64_t io_ns=0,io_commands=0;
inline void Begin(uint64_t seq,uint64_t key){if(Enabled()){State();front={true,seq,key,0,0,0,0};}}
inline void End(uint64_t begin,uint64_t end){if(!Enabled())return;
  State().Add(Row{{1,front.seq,front.key,Tid(),begin,end,front.wait,front.body,front.io,front.commands}});
  front.active=false;
}
inline uint64_t IoBegin(){return Enabled()?Now():0;}
inline void IoEnd(uint64_t begin){if(Enabled()){io_ns+=Now()-begin;++io_commands;}}
class Read {
  uint64_t file_,size_,begin_=0,locked_=0,io_=0,commands_=0;bool migration_;
 public:
  Read(uint64_t file,uint64_t size,bool migration):file_(file),size_(size),migration_(migration){}
  void BeforeLock(){if(Enabled()){begin_=Now();locked_=0;}}
  void Acquired(){if(Enabled()){locked_=Now();io_=io_ns;commands_=io_commands;
      if(front.active)front.wait+=locked_-begin_;}}
  void Done(){if(!Enabled())return;const auto end=Now();
    if(front.active){front.body+=end-locked_;front.io+=io_ns-io_;front.commands+=io_commands-commands_;}
    if(size_>=65536 || end-begin_>=1000000)
      State().Add(Row{{2,file_,size_,Tid(),begin_,locked_,end,io_ns-io_,io_commands-commands_,uint64_t(migration_)}});
  }
};
// 0生成值，1延迟向量/请求记录，2原生统计，3阶段输出。只保存慢区间及累计账。
class Work {
  unsigned kind_;uint64_t seq_,begin_=0;
 public:
  Work(unsigned kind,uint64_t seq):kind_(kind),seq_(seq){if(Enabled())begin_=Now();}
  ~Work(){if(!Enabled())return;const auto end=Now(),ns=end-begin_;auto& c=State().costs[kind_];
    c.calls.fetch_add(1,std::memory_order_relaxed);c.ns.fetch_add(ns,std::memory_order_relaxed);
    auto old=c.maximum.load();while(old<ns && !c.maximum.compare_exchange_weak(old,ns)){}
    if(ns>=1000000)State().Add(Row{{3,kind_,seq_,Tid(),begin_,end,0,0,0,0}});
  }
};
}
#endif
