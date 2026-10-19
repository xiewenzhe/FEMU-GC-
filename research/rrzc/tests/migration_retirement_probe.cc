// 提取当前真实迁移函数，使用内存替身注入删名；不访问设备，不模拟性能。
#include "rrzc_migration_window.h"
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

struct IOStatus {
  int code=0;
  static IOStatus OK() { return {}; }
  static IOStatus NotFound(const char*) { return {1}; }
  static IOStatus NoSpace(const char*) { return {2}; }
  bool ok() const { return code==0; }
  std::string ToString() const { return ok()?"OK":"合成状态"; }
};
template<class... Args> void Info(int,const char*,Args...) {}
bool ends_with(const std::string& value,const char* suffix) {
  const std::string s(suffix);
  return value.size()>=s.size() && value.compare(value.size()-s.size(),s.size(),s)==0;
}
struct Zone {
  uint64_t start_,wp_,used_capacity_,capacity_;
  bool IsEmpty() const { return wp_==start_; }
};
struct ZoneExtent {
  uint64_t start_,length_;Zone* zone_;
  ZoneExtent(uint64_t start,uint64_t length,Zone* zone):start_(start),length_(length),zone_(zone) {}
};
struct ZoneExtentSnapshot { std::string filename;uint64_t zone_start,start,length; };
struct Device {
  Zone source{0,1<<20,0,0},target{2<<20,3<<20,1<<19,1<<20};
  unsigned reset_calls=0,releases=0;uint64_t copied=0;
  Zone* GetIOZone(uint64_t zone) { return zone==source.start_?&source:&target; }
  bool IsMigrationReserveEnabled() const { return false; }
  unsigned GetBlockSize() const { return 512; }
  IOStatus TakeMigrateZone(Zone** target_zone,int,uint32_t) { *target_zone=&target;return {}; }
  IOStatus ReleaseMigrateZone(Zone*) { ++releases;return {}; }
  void AddGCBytesWritten(uint64_t bytes) { copied+=bytes; }
  IOStatus ResetUnusedIOZones() {
    ++reset_calls;
    if (!source.used_capacity_) { source.wp_=source.start_;source.capacity_=1<<20; }
    return {};
  }
};
struct ZoneFile {
  static constexpr unsigned SPARSE_HEADER_SIZE=8;
  std::vector<ZoneExtent*> extents;std::function<void()> after_copy;
  bool locked=false;
  ~ZoneFile() { for (auto* e:extents) {e->zone_->used_capacity_-=e->length_;delete e;} }
  bool TryAcquireWRLock() { assert(!locked);locked=true;return true; }
  void ReleaseWRLock() { assert(locked);locked=false; }
  auto GetExtents() const { return extents; }
  bool IsSparse() const { return false; }
  int GetWriteLifeTimeHint() const { return 0; }
  uint64_t GetID() const { return 1; }
  IOStatus MigrateData(uint64_t,uint32_t length,Zone* target) {
    const uint64_t physical=(length+511)/512*512;
    target->wp_+=physical;target->capacity_-=physical;
    if (after_copy) after_copy();
    return {};
  }
};
struct ZenFS {
  struct MigrationStats {uint64_t migrated_bytes=0,reset_zones=0;};
  Device* zbd_;int logger_=0;std::atomic<bool> v1_shadow_{true};
  rrzc_observe::MigrationWindow migration_window_{true};
  std::map<std::string,std::shared_ptr<ZoneFile>> files;
  explicit ZenFS(Device* device):zbd_(device) {}
  auto GetFile(const std::string& name) {
    auto i=files.find(name);return i==files.end()?std::shared_ptr<ZoneFile>{}:i->second;
  }
  IOStatus SyncFileExtents(ZoneFile* file,std::vector<ZoneExtent*> replacement) {
    for (size_t i=0;i<replacement.size();++i) {
      auto* old=file->extents[i];
      if (old->start_!=replacement[i]->start_) old->zone_->used_capacity_-=old->length_;
      delete old;
    }
    file->extents=std::move(replacement);return {};
  }
  IOStatus MigrateFileExtents(const std::string&,const std::vector<ZoneExtentSnapshot*>&,MigrationStats*);
  IOStatus MigrateExtents(const std::vector<ZoneExtentSnapshot*>&,MigrationStats*);
};

// 真实函数由已核验本地源提取；此文件没有替代被测迁移分支。
#include "migration-under-test.inc"

static void run_case(bool retire,bool keep_reader) {
  Device device;ZenFS fs(&device);const std::string name="/probe.sst";
  constexpr uint64_t bytes=513;
  const uint64_t initial_wp=device.target.wp_,initial_valid=device.target.used_capacity_;
  auto file=std::make_shared<ZoneFile>();std::weak_ptr<ZoneFile> weak=file;
  device.source.used_capacity_=bytes;
  file->extents.push_back(new ZoneExtent(0,bytes,&device.source));
  file->after_copy=[&] {
    if (retire) {
      fs.files.erase(name);
      // 删名不会析构迁移仍持有的文件对象，也不会提前扣除源有效量。
      assert(!weak.expired() && device.source.used_capacity_==bytes);
    }
  };
  fs.files[name]=file;
  std::shared_ptr<ZoneFile> reader=keep_reader?file:nullptr;file.reset();
  ZoneExtentSnapshot snapshot{name,0,0,bytes};ZenFS::MigrationStats stats;
  auto status=fs.MigrateExtents({&snapshot},&stats);
  assert(stats.migrated_bytes==bytes && device.copied==bytes && device.releases==1);
  assert(device.target.wp_==initial_wp+1024 && device.target.capacity_==(1<<20)-1024);
  if (retire) {
    assert(status.code==1 && device.reset_calls==0 && stats.reset_zones==0);
    assert(device.target.used_capacity_==initial_valid);
    assert(!device.source.IsEmpty());
    assert(device.source.used_capacity_==(keep_reader?bytes:0));
    assert(weak.expired()!=keep_reader);
    // 以下为替身中的后续清理，不是声称真实故障轮次执行过该调用。
    device.ResetUnusedIOZones();
    assert(device.source.IsEmpty()!=keep_reader);
    reader.reset();device.ResetUnusedIOZones();assert(device.source.IsEmpty());
  } else {
    assert(status.ok() && device.reset_calls==1 && stats.reset_zones==1);
    assert(device.target.used_capacity_==initial_valid+bytes && device.source.IsEmpty());
  }
  const auto frame=fs.migration_window_.Freeze();
  assert(frame.Get(0).active==0 && frame.Get(2<<20).active==0);
}
int main() {
  run_case(false,false);
  run_case(true,false);
  run_case(true,true);
  std::cout<<"当前真实迁移函数：正常路径、复制期删名、读句柄延迟释放的内存复现通过；未修改生产分支。\n";
}
