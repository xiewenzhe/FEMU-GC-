// 文件级在线读热特征；不读未来退役名单，不修改NAND RC或维护阈值。
#pragma once
#include <array>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <stdexcept>

namespace rrzc_stream {
inline constexpr uint64_t kStride=1024,kBuckets=64,kWindow=kStride*kBuckets;
inline std::atomic<uint64_t> io_sequence{0};
inline bool Enabled() {
  static const bool enabled=[] {
    const char* p=std::getenv("ZENFS_RRZC_STREAM_CLASSIFY");
    if(p && std::strcmp(p,"0") && std::strcmp(p,"1"))
      throw std::invalid_argument("在线流分类开关只能为0或1");
    return p && !std::strcmp(p,"1");
  }();
  return enabled;
}
inline uint64_t Now(){return io_sequence.load();}
inline uint64_t Tick(){return io_sequence.fetch_add(1)+1;}
struct Stats {uint64_t reads=0,reused=0,bytes=0;bool hot=false,changed=false;};
class Profile {
 public:
  Stats Read(uint64_t offset,uint64_t bytes,uint64_t file_bytes=0) {
    std::lock_guard<std::mutex> guard(mutex_);const auto event=Tick();
    auto& bucket=buckets_[(event/kStride)%kBuckets];
    if(bucket.epoch!=event/kStride){bucket={};bucket.epoch=event/kStride;}
    const auto page=offset/4096;bool reuse=false;Page* chosen=&pages_[0];
    for(auto& p:pages_) {
      if(p.last && p.page==page){chosen=&p;reuse=event-p.last<kWindow;break;}
      if(p.last<chosen->last)chosen=&p;
    }
    *chosen={page,event};++bucket.reads;bucket.reused+=reuse;bucket.bytes+=bytes;
    auto result=SnapshotLocked(event,file_bytes);result.changed=result.hot!=reported_hot_;
    reported_hot_=result.hot;return result;
  }
  Stats Snapshot(uint64_t file_bytes=0) const {
    std::lock_guard<std::mutex> guard(mutex_);return SnapshotLocked(Now(),file_bytes);
  }
 private:
  struct Bucket {uint64_t epoch=UINT64_MAX,reads=0,reused=0,bytes=0;};
  struct Page {uint64_t page=0,last=0;};
  Stats SnapshotLocked(uint64_t event,uint64_t file_bytes) const {
    Stats out;const auto epoch=event/kStride;
    for(const auto& b:buckets_)if(b.epoch<=epoch && epoch-b.epoch<kBuckets) {
      out.reads+=b.reads;out.reused+=b.reused;out.bytes+=b.bytes;
    }
    // 单次顺序Compaction通常不重复读同一小范围；持续重复读才认定H。
    // 也覆盖整个SST反复读取：16槽页表无法命中长于16页的循环扫描。
    out.hot=(out.reads>=256 && out.reused>=128) ||
            (file_bytes && out.reads>=128 && out.bytes/file_bytes>=4);
    return out;
  }
  mutable std::mutex mutex_;
  std::array<Bucket,kBuckets> buckets_{};
  std::array<Page,16> pages_{};
  bool reported_hot_=false;
};
}  // 分类窗口按最近约6.4万次SST底层读写计，不是10/30秒RC窗口。
