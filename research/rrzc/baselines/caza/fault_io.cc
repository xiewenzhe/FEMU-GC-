// Test-only preload library. Never used by the original program or production runs.
#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <dlfcn.h>
#include <cstdio>
#include <sys/types.h>
#include <unistd.h>

static std::atomic<bool> armed{false};
static std::atomic<uint64_t> fired{0}, failed_offset{0};
extern "C" void rrzc_fault_arm() { fired = 0; failed_offset = 0; armed = true; }
extern "C" uint64_t rrzc_fault_fired() { return fired.load(); }
extern "C" uint64_t rrzc_fault_offset() { return failed_offset.load(); }
static bool Fail(int fd, uint64_t offset) {
  if (!armed.load() || offset < 3 * (128ULL << 20) || offset >= (8ULL << 30)) return false;
  char link[64], name[256]; std::snprintf(link, sizeof(link), "/proc/self/fd/%d", fd);
  const auto length = readlink(link, name, sizeof(name) - 1);
  if (length < 0) return false;
  name[length] = '\0';
  if (std::strcmp(name, "/dev/nvme0n1") || !armed.exchange(false)) return false;
  failed_offset = offset; ++fired; errno = EIO; return true;
}
extern "C" ssize_t pwrite(int fd, const void* buffer, size_t size, off_t offset) {
  using Function = ssize_t (*)(int, const void*, size_t, off_t);
  static const auto next = reinterpret_cast<Function>(dlsym(RTLD_NEXT, "pwrite"));
  if (Fail(fd, offset)) return -1;
  if (!next) { errno = ENOSYS; return -1; }
  return next(fd, buffer, size, offset);
}
extern "C" ssize_t pwrite64(int fd, const void* buffer, size_t size, off64_t offset) {
  using Function = ssize_t (*)(int, const void*, size_t, off64_t);
  static const auto next = reinterpret_cast<Function>(dlsym(RTLD_NEXT, "pwrite64"));
  if (Fail(fd, offset)) return -1;
  if (!next) { errno = ENOSYS; return -1; }
  return next(fd, buffer, size, offset);
}
