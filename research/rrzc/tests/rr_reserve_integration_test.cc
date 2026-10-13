// 隔离客机专用：使用真实 ZenFS/FEMU，核验预留部分区可以成为 RR 源。
// payload.sst 是已知字节模式的文件系统测试载荷，不是 RocksDB 生成的 SST。
// 本程序不格式化设备、不改读数阈值、不直接调用 RR；依靠维护线程和真实读触发。
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "plugin/zenfs/fs/fs_zenfs.h"
#include "plugin/zenfs/fs/snapshot.h"

using namespace rocksdb;
constexpr size_t kChunk = 1 << 20;
constexpr size_t kFileSize = 8 * kChunk;
const std::string kPath = "/rr-reserve-test/payload.sst";

static void require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}
template <typename S> static void check(const S& s) {
  require(s.ok(), s.ToString());
}
static uint64_t mapped_zone(ZenFS& fs) {
  ZenFSSnapshot snapshot;
  ZenFSSnapshotOptions options;
  options.zone_ = options.zone_file_ = true;
  fs.GetZenFSSnapshot(snapshot, options);
  for (const auto& extent : snapshot.extents_) {
    if (extent.filename == kPath) {
      require(extent.length == kFileSize, "测试文件片段长度与预期不符");
      return extent.zone_start;
    }
  }
  return UINT64_MAX;
}
static void hot_reads(FSRandomAccessFile& file, char* buffer) {
  for (unsigned i = 0; i < 20; ++i) {
    Slice result;
    check(file.Read(0, 4096, IOOptions(), &result, buffer, nullptr));
    require(result.size() == 4096, "真实读取发生短读");
  }
}
static uint64_t wait_for_move(ZenFS& fs, ZonedBlockDevice& device,
                              uint64_t old_zone) {
  auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
  while (std::chrono::steady_clock::now() < deadline) {
    uint64_t current = mapped_zone(fs);
    if (current != UINT64_MAX && current != old_zone &&
        device.GetIOZone(old_zone)->IsEmpty()) {
      require(device.GetIOZone(current)->IsBusy() &&
              !device.GetIOZone(current)->IsFull(),
              "迁移后的载荷未处于持有独占权的部分预留区");
      return current;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
  }
  throw std::runtime_error("等待 RR 迁移并重置源区超时");
}
static void pressure(ZonedBlockDevice& device, uint64_t start, bool reset) {
  ZoneReadPressureReport report;
  check(device.GetZoneReadPressure(&report));
  require(report.read_threshold == 16, "设备不是本测试要求的阈值 16 功能配置");
  for (const auto& entry : report.zones) {
    if (entry.start != start) continue;
    std::cout << "设备读压力：Zone=" << start << "，最大读数="
              << entry.max_block_reads << "，达阈值块="
              << entry.blocks_at_threshold << std::endl;
    require(reset ? entry.max_block_reads == 0 : entry.blocks_at_threshold > 0,
            reset ? "源区重置后读数没有清零" : "真实读取尚未达到设备阈值");
    return;
  }
  throw std::runtime_error("设备报告中未找到测试源区");
}
static void check_tail_preservation(ZonedBlockDevice& device, uint64_t start) {
  // 通过占用其他空区构造无替补边界，不写满设备、不伪造容量或有效字节。
  struct EmptyZoneLocks {
    std::vector<Zone*> zones;
    ~EmptyZoneLocks() { for (auto* zone : zones) zone->CheckRelease(); }
  } held;
  held.zones.reserve(32);
  ZoneReadPressureReport report;
  check(device.GetZoneReadPressure(&report));
  for (const auto& entry : report.zones) {
    Zone* zone = device.GetIOZone(entry.start);
    if (zone == nullptr || !zone->Acquire()) continue;
    if (zone->IsEmpty()) held.zones.push_back(zone);
    else check(zone->CheckRelease());
  }
  require(!held.zones.empty(), "没有取得用于构造无替补边界的空区");
  Zone* reserved = device.GetIOZone(start);
  const uint64_t remaining = reserved->capacity_;
  const uint64_t original_wp = reserved->wp_;
  require(remaining + 4096 <= reserved->max_capacity_, "测试预留尾部尺寸不合要求");
  Zone* target = nullptr;
  check(device.TakeMigrateZone(&target, Env::WLTH_MEDIUM,
                               static_cast<uint32_t>(remaining + 4096)));
  if (target != nullptr) {
    check(device.ReleaseMigrateZone(target));
    throw std::runtime_error("无替补时超尾部请求意外获得目标");
  }
  require(reserved->capacity_ == remaining && reserved->wp_ == original_wp &&
          reserved->IsBusy() && !reserved->IsFull(),
          "无替补的大请求丢弃了原预留尾部或独占权");
  check(device.TakeMigrateZone(&target, Env::WLTH_MEDIUM, 4096));
  require(target != nullptr, "保留的尾部无法用于较小请求");
  const bool same_target = target == reserved;
  check(device.ReleaseMigrateZone(target));
  require(same_target, "较小请求没有复用原预留");
  std::cout << "真实预留尾部验证：占用其他空区=" << held.zones.size()
            << "，保留=" << remaining << " 字节；大请求无目标、小请求复用原预留"
            << std::endl;
}

static void check_diagnostic_deferrals(ZenFS& fs, ZonedBlockDevice& device,
                                       char* buffer, const FileOptions& options) {
  if (!device.IsMaintenanceDiagnosticsEnabled()) return;
  for (bool open_sst : {false, true}) {
    const std::string path = open_sst ? "/rr-reserve-test/open.sst"
                                    : "/rr-reserve-test/excluded.bin";
    std::unique_ptr<FSWritableFile> writer;
    check(fs.NewWritableFile(path, options, &writer, nullptr));
    writer->SetWriteLifeTimeHint(Env::WLTH_MEDIUM);
    // 超过设备 4 MiB 写缓存，让文件头自然建立 NAND 映射，避免仅测到缓存读。
    for (size_t offset = 0; offset < kFileSize; offset += kChunk) {
      check(writer->Append(Slice(buffer, kChunk), IOOptions(), nullptr));
    }
    check(writer->Sync(IOOptions(), nullptr));
    if (!open_sst) { check(writer->Close(IOOptions(), nullptr)); writer.reset(); }
    ZenFSSnapshot snapshot;
    ZenFSSnapshotOptions snapshot_options;
    snapshot_options.zone_ = snapshot_options.zone_file_ = true;
    fs.GetZenFSSnapshot(snapshot, snapshot_options);
    uint64_t start = UINT64_MAX;
    unsigned used_zones = 0;
    // 当前仅有本测试载荷；公开 Zone 快照仍可观察写锁中的有效容量。
    for (const auto& zone : snapshot.zones_) {
      if (zone.used_capacity == 0) continue;
      ++used_zones;
      require(zone.used_capacity == kFileSize, "诊断载荷混入了其他有效数据");
      start = zone.start;
    }
    require(used_zones == 1, "诊断测试没有唯一的有效源 Zone");
    uint64_t covered = 0;
    for (const auto& extent : snapshot.extents_) {
      if (extent.zone_start == start) covered += extent.length;
    }
    Zone* source = device.GetIOZone(start);
    const auto before = source->GetResetState();
    require(source->used_capacity_ == kFileSize && covered == (open_sst ? 0 : kFileSize),
            "诊断测试没有构造预期的 SST/覆盖条件");
    std::unique_ptr<FSRandomAccessFile> reader;
    check(fs.NewRandomAccessFile(path, options, &reader, nullptr));
    hot_reads(*reader, buffer); pressure(device, start, false);
    // 无其他载荷，等待至少一轮维护扫描；不直接调用 RR 或篡改读数。
    std::this_thread::sleep_for(std::chrono::seconds(12));
    require(source->GetResetState().generation == before.generation &&
            source->used_capacity_ == kFileSize, "不具资格的诊断载荷被错误刷新或重置");
    reader.reset();
    if (writer) { check(writer->Close(IOOptions(), nullptr)); writer.reset(); }
    // 只删除本测试刚创建的 8 MiB 文件，保留全部诊断日志。
    check(fs.DeleteFile(path, IOOptions(), nullptr));
    require(source->GetResetState().generation == before.generation + 1,
            "删除诊断载荷后成功重置未推进主机代际");
    std::cout << "真实资格诊断：Zone=" << start << "，类型="
              << (open_sst ? "写入中 SST，覆盖不足" : "非 SST，覆盖完整")
              << "；维护延后，删除后代际=" << source->GetResetState().generation << std::endl;
  }
}
int main(int argc, char** argv) {
  try {
    require(argc == 2 && std::string(argv[1]).rfind("/tmp/rrzc-", 0) == 0,
            "须提供隔离测试的 /tmp/rrzc- 日志目录");
    require(getenv("ZENFS_RR_ENABLE") &&
            std::string(getenv("ZENFS_RR_ENABLE")) == "1" &&
            getenv("ZENFS_MIGRATE_RESERVE") &&
            std::string(getenv("ZENFS_MIGRATE_RESERVE")) == "1",
            "须显式开启 RR 与迁移预留");
    std::shared_ptr<Logger> logger;
    require(!FileSystem::Default()->FileExists(
                std::string(argv[1]) + "/zenfs.integration.log", IOOptions(), nullptr).ok(),
            "测试日志已存在，停止以避免覆盖");
    check(Env::Default()->NewLogger(std::string(argv[1]) + "/zenfs.integration.log", &logger));
    logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned_device = std::make_unique<ZonedBlockDevice>(
        "nvme0n1", ZbdBackendType::kBlockDev, logger);
    check(owned_device->Open(false, true));
    auto* device = owned_device.get();
    auto fs = std::make_unique<ZenFS>(owned_device.release(), FileSystem::Default(), logger);
    check(fs->Mount(false));
    require(device->GetUsedSpace() == 0, "实验 IO 区已有有效数据，停止以避免混入旧数据");
    check(fs->CreateDir("/rr-reserve-test", IOOptions(), nullptr));
    void* raw_buffer = nullptr;
    require(posix_memalign(&raw_buffer, 4096, kChunk) == 0, "无法申请对齐测试缓冲区");
    std::unique_ptr<char, decltype(&std::free)> buffer(static_cast<char*>(raw_buffer), &std::free);
    for (size_t i = 0; i < kChunk; ++i) buffer.get()[i] = static_cast<char>((i * 131 + 17) % 251);
    FileOptions options;
    options.use_direct_writes = options.use_direct_reads = true;
    check_diagnostic_deferrals(*fs, *device, buffer.get(), options);
    std::unique_ptr<FSWritableFile> writer;
    check(fs->NewWritableFile(kPath, options, &writer, nullptr));
    writer->SetWriteLifeTimeHint(Env::WLTH_MEDIUM);
    for (size_t i = 0; i < kFileSize; i += kChunk) {
      check(writer->Append(Slice(buffer.get(), kChunk), IOOptions(), nullptr));
    }
    check(writer->Close(IOOptions(), nullptr));
    writer.reset();
    uint64_t initial = mapped_zone(*fs);
    require(initial != UINT64_MAX, "写入后未找到测试映射");
    Zone* source = device->GetIOZone(initial);
    const uint64_t initial_generation = source->GetResetState().generation;
    require(!source->IsBusy() && !source->IsFull(), "初始载荷不是普通部分区");
    bool prepared = false; uint64_t sacrificed = 0;
    // 测试预置：仅封闭普通源，确保第一次 RR 没有其他部分目标可用。
    check(device->PrepareReadReclaimSource(source, source->used_capacity_.load(),
                                           source->wp_, &prepared, &sacrificed));
    require(prepared, "无法预封闭普通测试源");
    std::cout << "预置普通源封闭：Zone=" << initial << "，剩余=" << sacrificed << std::endl;
    std::unique_ptr<FSRandomAccessFile> reader;
    check(fs->NewRandomAccessFile(kPath, options, &reader, nullptr));
    hot_reads(*reader, buffer.get()); pressure(*device, initial, false);
    uint64_t first = wait_for_move(*fs, *device, initial);
    require(source->GetResetState().generation == initial_generation + 1,
            "第一次 RR 成功重置未推进主机代际");
    pressure(*device, initial, true);
    std::cout << "第一次真实 RR：普通源=" << initial << "，部分预留目标=" << first << std::endl;
    check_tail_preservation(*device, first);
    const uint64_t first_generation = device->GetIOZone(first)->GetResetState().generation;
    hot_reads(*reader, buffer.get()); pressure(*device, first, false);
    uint64_t second = wait_for_move(*fs, *device, first);
    require(device->GetIOZone(first)->GetResetState().generation == first_generation + 1,
            "第二次 RR 成功重置未推进主机代际");
    pressure(*device, first, true);
    std::cout << "第二次真实 RR：预留源=" << first << "，新的部分预留目标=" << second << std::endl;
    for (size_t offset = 0; offset < kFileSize; offset += kChunk) {
      Slice result;
      check(reader->Read(offset, kChunk, IOOptions(), &result, buffer.get(), nullptr));
      require(result.size() == kChunk, "迁移后完整校验发生短读");
      for (size_t i = 0; i < kChunk; ++i) {
        require(result[i] == static_cast<char>((i * 131 + 17) % 251), "迁移后字节内容不一致");
      }
    }
    reader.reset();
    device->LogZoneStats();
    std::cout << "两次 RR 后全部 8 MiB 字节校验通过；这是文件系统功能验证，不是性能实验" << std::endl;
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "RR 预留兼容测试失败：" << error.what() << std::endl;
    return 1;
  }
}
