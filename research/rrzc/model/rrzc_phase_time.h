#ifndef RRZC_PHASE_TIME_H
#define RRZC_PHASE_TIME_H

// 只读区间诊断：默认关闭，内存缓冲，进程正常退出时一次写出。
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <mutex>
#include <vector>
#include <sys/syscall.h>
#include <unistd.h>

namespace rrzc_phase_time {
enum Kind : uint64_t {
  kFlush = 1, kCompactWait = 2, kGcGate = 3, kLayout = 4,
  kAllocate = 5, kMigrateTarget = 6, kOpenToken = 7,
  kReset = 8, kFinish = 9, kMigrateExtents = 10,
  kStrictRR = 11, kSpaceRecovery = 12, kRRCheck = 13
};
struct Record { uint64_t kind, begin, end, tid, object, completed; };
static_assert(sizeof(Record) == 48, "诊断格式必须为六个64位整数");
inline uint64_t Now() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
}
struct Buffer {
  FILE* file = nullptr;
  std::mutex mutex;
  std::vector<Record> records;
  uint64_t dropped = 0;
  Buffer() {
    const char* path = std::getenv("RRZC_PHASE_TIME");
    if (path && *path) {
      file = std::fopen(path, "wbx");
      if (!file) { std::perror("无法创建区间计时证据"); std::abort(); }
      records.reserve(16384);
    }
  }
  ~Buffer() {
    if (!file) return;
    bool ok = std::fwrite("RRZCTM01", 1, 8, file) == 8;
    ok = (std::fwrite(&dropped, sizeof(dropped), 1, file) == 1) && ok;
    ok = (std::fwrite(records.data(), sizeof(Record), records.size(), file) == records.size()) && ok;
    ok = (std::fclose(file) == 0) && ok;
    if (!ok || dropped) std::fprintf(stderr, "区间计时证据不完整，丢弃=%llu\n", (unsigned long long)dropped);
  }
};
inline Buffer& State() { static Buffer value; return value; }
class Scope {
  Buffer& buffer_;
  Record record_{};
 public:
  explicit Scope(uint64_t kind, uint64_t object = 0, uint64_t completed = UINT64_MAX)
      : buffer_(State()) {
    if (!buffer_.file) return;
    static thread_local uint64_t tid = static_cast<uint64_t>(::syscall(SYS_gettid));
    record_ = {kind, Now(), 0, tid, object, completed};
  }
  ~Scope() {
    if (!buffer_.file) return;
    record_.end = Now();
    std::lock_guard<std::mutex> lock(buffer_.mutex);
    if (buffer_.records.size() < (1u << 20)) buffer_.records.push_back(record_);
    else ++buffer_.dropped;
  }
  Scope(const Scope&) = delete;
  Scope& operator=(const Scope&) = delete;
};
}
#endif
