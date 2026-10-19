// 从修复源镜像提取真实OpenWritableFile；仅设备、元数据和文件对象为内存替身。
// 有界构造迁移持文件锁、重开等待的交错，同时验证删名／改名／替换后的身份。
#include <atomic>
#include <cassert>
#include <chrono>
#include <functional>
#include <future>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <ctime>

struct IOStatus {
  enum Code { OK_CODE, NOT_FOUND, ERROR } code=OK_CODE;
  bool ok() const { return code==OK_CODE; }
  static IOStatus OK() { return {}; }
  static IOStatus NotFound(const char*) { return {NOT_FOUND}; }
};
enum class IOType { kWAL, kUnknown };
struct FileOptions { bool use_direct_writes=true; int io_options=0; };
struct IODebugContext {};
struct ZonedBlockDevice {
  unsigned resets=0;
  IOStatus ResetUnusedIOZones() { ++resets; return {}; }
};
struct ZoneFile {
  std::mutex open_for_wr_mtx_;
  bool deleted=false, sparse=false;
  IOType type=IOType::kUnknown;
  std::function<void()> entering;
  ZoneFile(ZonedBlockDevice*,uint64_t,int*) {}
  void AcquireWRLock() { if (entering) entering(); open_for_wr_mtx_.lock(); }
  void ReleaseWRLock() { open_for_wr_mtx_.unlock(); }
  bool IsDeleted() const { return deleted; }
  void SetFileModificationTime(time_t) {}
  void AddLinkName(const std::string&) {}
  void SetIOType(IOType t) { type=t; }
  void SetSparse(bool value) { sparse=value; }
};
struct FSWritableFile { virtual ~FSWritableFile()=default; };
struct ZonedWritableFile:FSWritableFile {
  std::shared_ptr<ZoneFile> file;
  ZonedWritableFile(ZonedBlockDevice*,bool,std::shared_ptr<ZoneFile> f):file(std::move(f)) {}
  ~ZonedWritableFile() override { file->ReleaseWRLock(); }
};
static bool ends_with(const std::string& name,const std::string& end) {
  return name.size()>=end.size() && name.compare(name.size()-end.size(),end.size(),end)==0;
}
struct ZenFS {
  std::mutex files_mtx_;
  std::map<std::string,std::shared_ptr<ZoneFile>> files_;
  ZonedBlockDevice device;
  ZonedBlockDevice* zbd_=&device;
  uint64_t next_file_id_=1;
  int metadata_writer_=0;
  bool fail_metadata=false;
  static std::string FormatPathLexically(const std::string& s) { return s; }
  std::shared_ptr<ZoneFile> GetFileNoLock(const std::string& name) {
    auto i=files_.find(name); return i==files_.end()?nullptr:i->second;
  }
  IOStatus DeleteFileNoLock(const std::string& name,int,IODebugContext*) {
    files_.erase(name); return {};
  }
  IOStatus SyncFileMetadataNoLock(std::shared_ptr<ZoneFile>) { return fail_metadata?IOStatus{IOStatus::ERROR}:IOStatus{}; }
  IOStatus OpenWritableFile(const std::string&,const FileOptions&,std::unique_ptr<FSWritableFile>*,IODebugContext*,bool);
};
#include "reopen-under-test.inc"

static unsigned checks=0;
#define CHECK(x) do { ++checks; assert(x); } while (0)

static void contested(unsigned mutation,bool old) {
  ZenFS fs;
  auto file=std::make_shared<ZoneFile>(fs.zbd_,1,&fs.metadata_writer_);
  fs.files_["/file.sst"]=file;
  file->AcquireWRLock();
  std::promise<void> attempted;
  auto ready=attempted.get_future();
  file->entering=[&] { attempted.set_value(); };
  IOStatus result;
  std::unique_ptr<FSWritableFile> handle;
  bool returned=false;
  std::shared_ptr<ZoneFile> returned_file;
  std::thread opener([&] {
    result=fs.OpenWritableFile("/file.sst",{},&handle,nullptr,true);
    returned=bool(handle);
    if (handle) returned_file=static_cast<ZonedWritableFile*>(handle.get())->file;
    // 开写锁由重开线程取得，也必须在该线程关闭句柄并释放。
    handle.reset();
  });
  const bool reached=ready.wait_for(std::chrono::seconds(2))==std::future_status::ready;
  // 不阻塞取得第二把锁，避免测试旧缺陷时把测试本身挂死。
  std::unique_lock<std::mutex> table(fs.files_mtx_,std::try_to_lock);
  const bool available=table.owns_lock();
  if (available && mutation) {
    if (mutation==1) { fs.files_.erase("/file.sst"); file->deleted=true; }
    if (mutation==2) { fs.files_.erase("/file.sst"); fs.files_["/renamed.sst"]=file; }
    if (mutation==3) fs.files_["/file.sst"]=std::make_shared<ZoneFile>(fs.zbd_,2,&fs.metadata_writer_);
    if (mutation==4) file->deleted=true;
  }
  if (table.owns_lock()) table.unlock();
  file->ReleaseWRLock();
  opener.join();
  CHECK(reached);
  CHECK(available==!old);
  CHECK(result.ok()==(!mutation || old));
  CHECK(returned==result.ok());
  if (returned) CHECK(returned_file==file);
  // 身份变化返回时必须已经释放旧文件锁；由主线程尝试，避免递归加锁。
  CHECK(file->open_for_wr_mtx_.try_lock());
  file->open_for_wr_mtx_.unlock();
  CHECK(fs.device.resets==0);
}

int main(int argc,char** argv) {
  const bool old=argc==2 && std::string(argv[1])=="--expect-old";
  if (old) {
    contested(0,true);
    std::cout<<"旧真实函数回归：确认持表锁等待，复现锁序缺陷；主动解锁后退出\n";
    return 0;
  }
  CHECK(argc==1);
  for (unsigned repeat=0;repeat<20;++repeat)
    for (unsigned mutation=0;mutation<=4;++mutation) contested(mutation,false);
  for (bool reopen:{false,true}) {
    ZenFS fs;
    std::unique_ptr<FSWritableFile> handle;
    CHECK(fs.OpenWritableFile("/new.sst",{},&handle,nullptr,reopen).ok());
    CHECK(bool(handle) && fs.files_.size()==1);
    handle.reset();
    fs.fail_metadata=true;
    CHECK(!fs.OpenWritableFile("/fail.sst",{},&handle,nullptr,reopen).ok());
    CHECK(!handle && fs.files_.count("/fail.sst")==0);
  }
  std::cout<<"修复真实重开函数："<<checks<<"项内存检查通过；含100次可控并发，不访问设备\n";
}
