// Exercise the exact replay implementation against a small I/O test backend.
// This does not substitute for compiling/running the actual ZenFS backend.
#include "rrzc_caza_bridge.h"
#include <cassert>
#include <cstring>
#include <cstdlib>
#include <iostream>

struct IOStatus {
  bool good = true;
  bool ok() const { return good; }
  static IOStatus OK() { return {}; }
  static IOStatus IOError(const std::string&) { return {false}; }
  static IOStatus InvalidArgument(const std::string&) { return {false}; }
  static IOStatus NoSpace(const std::string&) { return {false}; }
  static IOStatus Corruption(const std::string&) { return {false}; }
};
struct Slice {
  const char* bytes; size_t length;
  Slice(const std::string& s) : bytes(s.data()), length(s.size()) {}
  Slice(const char* p, size_t n) : bytes(p), length(n) {}
  const char* data() const { return bytes; }
  size_t size() const { return length; }
};
struct FakeZone {
  uint64_t start_ = 100;
  struct Reset { bool known = true; uint64_t generation = 0; };
  Reset reset;
  Reset GetResetState() const { return reset; }
};
struct Extent { FakeZone* zone_; };
struct ZoneFile {
  void* zbd_ = this;
  std::string name;
  std::vector<std::string> linkfiles_;
  std::vector<Extent*> extents_;
  std::string written;
  unsigned calls = 0, fail_at = 0;
  uint64_t size = 0;
  static constexpr uint64_t NO_EXTENT = UINT64_MAX;
  uint64_t extent_start_ = 0;
  unsigned pushes = 0, releases = 0, closes = 0;
  bool fail_persist = false;
  explicit ZoneFile(std::string n) : name(std::move(n)), linkfiles_{name} {}
  const std::string& GetFilename() const { return name; }
  void* GetZbd() const { return zbd_; }
  uint64_t GetFileSize() const { return size; }
  void SetFileSize(uint64_t value) { size = value; }
  IOStatus Append(void* data, size_t count) {
    ++calls;
    if (calls == fail_at) return IOStatus::IOError("injected");
    assert(rrzc_caza::current != nullptr);
    written.append(static_cast<char*>(data), count); size += count; return {};
  }
  IOStatus CazaBind();
  IOStatus CazaAbort();
  void PushExtent() { ++pushes; }
  IOStatus PersistMetadata() { return {!fail_persist}; }
  void ReleaseWRLock() { ++releases; }
  IOStatus CloseActiveZone() { ++closes; return {}; }
};
struct ZonedWritableFile {
  ZoneFile* zoneFile_;
  std::mutex buffer_mtx_;
  std::shared_ptr<rrzc_caza::Pending> caza_pending_;
  std::vector<std::string> caza_buffers_;
  uint64_t caza_bytes_ = 0, caza_truncate_ = 0, wp = 0;
  unsigned block_sz = 4096;
  bool caza_drained_ = false, caza_truncate_known_ = false, buffered = false;
  IOStatus caza_error_;
  std::string buffer;
  explicit ZonedWritableFile(ZoneFile& file) : zoneFile_(&file) {
    caza_pending_ = rrzc_caza::Bridge().Register(file.name);
  }
  ~ZonedWritableFile() { rrzc_caza::Bridge().Unregister(zoneFile_->name, caza_pending_); }
  IOStatus CazaBuffer(const Slice&, uint64_t);
  IOStatus CazaDrain();
  IOStatus BufferedWrite(const Slice& data) {
    buffer.append(data.data(), data.size()); return {};
  }
  IOStatus FlushBuffer() {
    auto s = zoneFile_->Append(buffer.data(), buffer.size());
    if (s.ok()) { wp += buffer.size(); buffer.clear(); }
    return s;
  }
};

#include "writable_methods.inc"

static void Ready(ZoneFile& file) {
  rrzc_caza::Bridge().Ready(file.name, 0, "0000", "0009");
}
int main() {
  assert(rrzc_caza::Enabled());
  rrzc_caza::Bridge().Publish("/db", {});
  const std::string bytes(4096, 'x');
  for (bool buffered : {false, true}) {
    ZoneFile file("/db/000001.sst");
    ZonedWritableFile writer(file); writer.buffered = buffered;
    assert(writer.CazaBuffer(Slice(bytes), 0).ok());
    assert(writer.CazaBuffer(Slice(bytes), bytes.size()).ok());
    assert(file.calls == 0 && file.written.empty() && writer.wp == 8192);
    assert(!writer.CazaDrain().ok() && file.calls == 0);
    Ready(file); writer.caza_truncate_known_ = true; writer.caza_truncate_ = 8000;
    assert(writer.CazaDrain().ok() && file.written == bytes + bytes);
    assert(writer.wp == 8192 && file.GetFileSize() == 8000);
    const auto calls = file.calls;
    assert(writer.CazaDrain().ok() && file.calls == calls);
    assert(!writer.CazaBuffer(Slice(bytes), 8192).ok());
    assert(rrzc_caza::current == nullptr);
  }
  {
    ZoneFile file("/db/000002.sst"); file.fail_at = 2;
    ZonedWritableFile writer(file);
    assert(writer.CazaBuffer(Slice(bytes), 0).ok());
    assert(writer.CazaBuffer(Slice(bytes), 4096).ok()); Ready(file);
    assert(!writer.CazaDrain().ok() && file.calls == 2 && file.written == bytes);
    assert(!writer.CazaDrain().ok() && file.calls == 2);  // Never repeat a partial write.
    assert(rrzc_caza::current == nullptr);
  }
  {
    ZoneFile file("/db/000003.sst"); ZonedWritableFile writer(file);
    assert(!writer.CazaBuffer(Slice(nullptr, (256ULL << 20) + 1), 0).ok());
    Ready(file); assert(!writer.CazaDrain().ok() && file.calls == 0);
  }
  {
    ZoneFile file("/db/000004.sst"); ZonedWritableFile writer(file);
    assert(!writer.CazaBuffer(Slice(bytes), 1).ok()); Ready(file);
    assert(!writer.CazaDrain().ok() && file.calls == 0);
  }
  {
    ZoneFile file("/db/000005.sst"); file.fail_persist = true;
    assert(!file.CazaAbort().ok());
    assert(file.pushes == 1 && file.releases == 1 && file.closes == 1);
    assert(file.extent_start_ == ZoneFile::NO_EXTENT);
  }
  {
    ZoneFile file("/db/000006.sst");
    file.linkfiles_.push_back("/db/000007.sst");
    assert(!file.CazaBind().ok());  // Unsupported mapping returns status, never escapes the worker.
    file.linkfiles_.pop_back();
    FakeZone zone; zone.reset.known = false;
    Extent extent{&zone}; file.extents_.push_back(&extent);
    assert(!file.CazaBind().ok());
    zone.reset.known = true;
    assert(file.CazaBind().ok());
  }
  std::cout << "CAZA bounded staging/replay/error tests passed; fake I/O backend only\n";
}
