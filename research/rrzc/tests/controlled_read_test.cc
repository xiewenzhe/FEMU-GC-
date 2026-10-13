// 隔离客机专用：受控冷热读取和 RR 成本观察，不是最终 ZC 性能实验。
// 两个 .sst 是已知字节模式载荷，不是 RocksDB 生成的 SST。
// 不格式化设备、不改生产策略、不直接调用 RR；小负载不会触发空间型 ZC。
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "plugin/zenfs/fs/snapshot.h"

using namespace rocksdb;
constexpr uint64_t kChunk = 1 << 20;
const std::string kPaths[] = {"/controlled-read/single.sst", "/controlled-read/eight.sst"};
constexpr uint64_t kSizes[] = {32 * kChunk, 128 * kChunk};
static void require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}
template <typename S> static void check(const S& status) {
  require(status.ok(), status.ToString());
}
static unsigned char pattern(uint64_t offset) {
  return ((offset % kChunk) * 131 + 17) % 251;
}
static std::optional<ZoneExtentSnapshot> find_extent(ZenFS& fs, const std::string& path) {
  ZenFSSnapshot snapshot;
  ZenFSSnapshotOptions options;
  options.zone_ = options.zone_file_ = true;
  fs.GetZenFSSnapshot(snapshot, options);
  unsigned found = 0;
  std::optional<ZoneExtentSnapshot> result;
  for (const auto& item : snapshot.extents_) {
    if (item.filename != path) continue;
    result = item;
    ++found;
  }
  require(found <= 1, "载荷出现多个片段，不能沿用本测试的单片段条件");
  return result;
}
static ZoneExtentSnapshot extent(ZenFS& fs, const std::string& path, uint64_t size) {
  auto result = find_extent(fs, path);
  require(result && result->length == size, "载荷不是预期的单片段映射");
  return *result;
}
static ZoneReadPressure pressure(ZonedBlockDevice& device, uint64_t zone, uint64_t threshold) {
  ZoneReadPressureReport report;
  check(device.GetZoneReadPressure(&report));
  require(report.read_threshold == threshold && report.blocks_per_zone == 64 && report.zones.size() == 32,
          "设备阈值或几何与本次实验配置不符");
  for (const auto& item : report.zones) if (item.start == zone) return item;
  throw std::runtime_error("设备报告没有目标 Zone");
}
static void read(FSRandomAccessFile& file, char* buffer, uint64_t offset, size_t size) {
  Slice value;
  check(file.Read(offset, size, IOOptions(), &value, buffer, nullptr));
  require(value.size() == size, "真实读取发生短读");
  for (size_t i = 0; i < size; ++i)
    require(static_cast<unsigned char>(value[i]) == pattern(offset + i), "读取载荷字节不符");
}
static void write(ZenFS& fs, const std::string& path, uint64_t size, char* buffer,
                  const FileOptions& options) {
  require(!fs.FileExists(path, IOOptions(), nullptr).ok(), "测试文件已存在，拒绝覆盖");
  for (uint64_t i = 0; i < kChunk; ++i) buffer[i] = pattern(i);
  std::unique_ptr<FSWritableFile> writer;
  check(fs.NewWritableFile(path, options, &writer, nullptr));
  writer->SetWriteLifeTimeHint(Env::WLTH_MEDIUM);
  for (uint64_t i = 0; i < size; i += kChunk)
    check(writer->Append(Slice(buffer, kChunk), IOOptions(), nullptr));
  check(writer->Close(IOOptions(), nullptr));
}
static void seal(ZonedBlockDevice& device, uint64_t zone) {
  Zone* source = device.GetIOZone(zone);
  require(source != nullptr && !source->IsFull(), "测试源区状态不符合预置条件");
  bool prepared = false;
  uint64_t sacrificed = 0;
  check(device.PrepareReadReclaimSource(source, source->used_capacity_.load(), source->wp_,
                                       &prepared, &sacrificed));
  require(prepared && source->IsFull(), "测试预置封闭源区失败");
  std::cout << "测试预置封闭：Zone=" << zone << "，牺牲尾部字节=" << sacrificed << std::endl;
}
int main(int argc, char** argv) {
  try {
    require(argc == 4, "用法：程序 prepare|rr|verify 阈值 /tmp/rrzc-独立目录");
    const std::string mode = argv[1], out = argv[3];
    require(mode == "prepare" || mode == "rr" || mode == "verify", "模式不支持");
    const uint64_t threshold = std::stoull(argv[2]);
    require(threshold == 256 || threshold == 1024 || threshold == 4096, "仅接受本轮预先指定的三个探索阈值");
    require(out.rfind("/tmp/rrzc-", 0) == 0 && std::filesystem::is_directory(out), "须先创建隔离结果目录");
    require(getenv("ZENFS_RR_ENABLE") && std::string(getenv("ZENFS_RR_ENABLE")) == (mode == "rr" ? "1" : "0"),
            "RR 开关须与阶段一致");
    require(getenv("ZENFS_MIGRATE_RESERVE") && std::string(getenv("ZENFS_MIGRATE_RESERVE")) == "1",
            "须显式使用已验证的迁移预留版本");
    const std::string log = out + "/zenfs." + mode + ".log";
    require(!std::filesystem::exists(log), "阶段日志已存在，拒绝覆盖");
    std::shared_ptr<Logger> logger;
    check(Env::Default()->NewLogger(log, &logger));
    logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned = std::make_unique<ZonedBlockDevice>("nvme0n1", ZbdBackendType::kBlockDev, logger);
    check(owned->Open(false, true));
    auto* device = owned.get();
    auto fs = std::make_unique<ZenFS>(owned.release(), FileSystem::Default(), logger);
    check(fs->Mount(false));
    FileOptions options;
    options.use_direct_reads = options.use_direct_writes = true;
    void* raw = nullptr;
    require(posix_memalign(&raw, 4096, kChunk) == 0, "申请对齐缓冲区失败");
    std::unique_ptr<char, decltype(&std::free)> buffer(static_cast<char*>(raw), &std::free);
    if (mode == "prepare") {
      require(device->GetUsedSpace() == 0, "IO 区存在旧数据，拒绝准备载荷");
      require(!std::filesystem::exists(out + "/sources.tsv"), "源区清单已存在，拒绝覆盖");
      check(fs->CreateDir("/controlled-read", IOOptions(), nullptr));
      const std::string audit = "/controlled-read/audit.bin";
      write(*fs, audit, 8 * kChunk, buffer.get(), options);
      auto audit_extent = extent(*fs, audit, 8 * kChunk);
      auto previous = pressure(*device, audit_extent.zone_start, threshold);
      std::unique_ptr<FSRandomAccessFile> reader;
      check(fs->NewRandomAccessFile(audit, options, &reader, nullptr));
      // 首段已由大于写缓存的载荷建立映射；不把缓存读当作 NAND 计数。
      for (unsigned i = 0; i < 4; ++i) read(*reader, buffer.get(), i * 4096, 4096);
      auto after = pressure(*device, audit_extent.zone_start, threshold);
      require(after.sum_block_reads == previous.sum_block_reads + 4, "四次 4 KiB 读的计数增量不是 4");
      previous = after;
      read(*reader, buffer.get(), 0, 16384);
      after = pressure(*device, audit_extent.zone_start, threshold);
      require(after.sum_block_reads == previous.sum_block_reads + 4, "一次 16 KiB 读的计数增量不是 4");
      previous = after;
      read(*reader, buffer.get(), 0, 65536);
      after = pressure(*device, audit_extent.zone_start, threshold);
      require(after.sum_block_reads == previous.sum_block_reads + 16, "一次 64 KiB 读的计数增量不是 16");
      std::cout << "真实计数口径通过：四次4KiB增加4，一次16KiB增加4，一次64KiB增加16" << std::endl;
      reader.reset();
      // 仅删除本程序刚创建的 8 MiB 审计文件，日志保留；不删除其他数据。
      check(fs->DeleteFile(audit, IOOptions(), nullptr));
      require(pressure(*device, audit_extent.zone_start, threshold).sum_block_reads == 0, "审计源区清空重置后计数未归零");
      uint64_t zones[2];
      std::unique_ptr<FSRandomAccessFile> readers[2];
      std::ofstream manifest(out + "/sources.tsv");
      require(manifest.good(), "无法写入源区清单");
      for (unsigned i = 0; i < 2; ++i) {
        write(*fs, kPaths[i], kSizes[i], buffer.get(), options);
        auto item = extent(*fs, kPaths[i], kSizes[i]);
        zones[i] = item.zone_start;
        require(item.start == item.zone_start, "载荷起点非 Zone 起点，不能沿用受控映射步长");
        seal(*device, zones[i]);
        manifest << zones[i] << '\t' << kSizes[i] << '\n';
        check(fs->NewRandomAccessFile(kPaths[i], options, &readers[i], nullptr));
        require(pressure(*device, zones[i], threshold).sum_block_reads == 0, "冷热读源区存在非预期初始读数");
      }
      require(zones[0] != zones[1], "两个载荷未分离到不同 Zone");
      manifest.close();
      std::ofstream csv(out + "/pressure.csv");
      require(csv.good(), "无法写入压力记录");
      csv << "阶段轮数,分布,Zone字节地址,有效字节,累计4KiB读次数,最大读数,读数总和,80%风险块,90%风险块,达阈值块\n";
      uint64_t completed = 0;
      for (uint64_t target : {uint64_t(0), threshold / 4, threshold / 2, threshold * 4 / 5, threshold * 9 / 10, threshold}) {
        for (; completed < target; ++completed) {
          // QLC 每平面连续写入 4 个16KiB页；64KiB步长跨物理 block。
          // 实际 1/8 块分布还须通过 C1h 最大值、总和和阈值块数交叉验证。
          for (unsigned j = 0; j < 8; ++j) read(*readers[0], buffer.get(), 0, 4096);
          for (unsigned j = 0; j < 8; ++j) read(*readers[1], buffer.get(), j * 65536, 4096);
        }
        for (unsigned i = 0; i < 2; ++i) {
          const auto p = pressure(*device, zones[i], threshold);
          require(p.sum_block_reads == 8 * target && p.max_block_reads == (i == 0 ? 8 * target : target),
                  "受控读分布的最大值或总和不符合预期");
          if (target == threshold) require(p.blocks_at_threshold == (i == 0 ? 1 : 8), "达阈值块不是预期的 1/8");
          csv << target << ',' << (i == 0 ? "单热块" : "八热块") << ',' << zones[i] << ',' << kSizes[i]
              << ',' << 8 * target << ',' << p.max_block_reads << ',' << p.sum_block_reads << ','
              << p.blocks_ge_80pct << ',' << p.blocks_ge_90pct << ',' << p.blocks_at_threshold << '\n';
        }
      }
      std::cout << "受控分布通过：阈值=" << threshold << "，两区各读=" << 8 * threshold
                << "次，达阈值块=1/8，有效字节=33554432/134217728；准备阶段RR关闭" << std::endl;
    } else if (mode == "rr") {
      std::ifstream manifest(out + "/sources.tsv");
      uint64_t zones[2], sizes[2];
      require(bool(manifest >> zones[0] >> sizes[0] >> zones[1] >> sizes[1]), "源区清单无法读取");
      for (unsigned i = 0; i < 2; ++i) {
        require(sizes[i] == kSizes[i] && extent(*fs, kPaths[i], kSizes[i]).zone_start == zones[i], "RR 前源区身份改变");
        const auto p = pressure(*device, zones[i], threshold);
        require(p.sum_block_reads == 8 * threshold && p.blocks_at_threshold == (i == 0 ? 1 : 8), "RR 前计数与准备阶段不一致");
      }
      bool moved[2] = {false, false};
      const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(60);
      while ((!moved[0] || !moved[1]) && std::chrono::steady_clock::now() < deadline) {
        for (unsigned i = 0; i < 2; ++i) if (!moved[i]) {
          // 迁移持有写锁时快照可暂时不包含文件；这是未观察，不是文件丢失。
          auto current = find_extent(*fs, kPaths[i]);
          if (!current || current->zone_start == zones[i]) continue;
          require(current->length == kSizes[i], "迁移完成后的片段长度不符");
          if (!device->GetIOZone(zones[i])->IsEmpty()) continue;
          require(pressure(*device, zones[i], threshold).sum_block_reads == 0, "RR 重置源区后计数没有归零");
          moved[i] = true;
          std::cout << "真实RR完成：分布=" << (i == 0 ? "单热块" : "八热块") << "，源Zone=" << zones[i]
                    << "，有效字节=" << kSizes[i] << "，源计数归零" << std::endl;
        }
        if (!moved[0] || !moved[1]) std::this_thread::sleep_for(std::chrono::milliseconds(100));
      }
      require(moved[0] && moved[1], "等待两区实际 RR 完成超时");
    } else {
      for (unsigned i = 0; i < 2; ++i) {
        uint64_t size = 0;
        check(fs->GetFileSize(kPaths[i], IOOptions(), &size, nullptr));
        require(size == kSizes[i], "RR 后文件长度改变");
        std::unique_ptr<FSRandomAccessFile> reader;
        check(fs->NewRandomAccessFile(kPaths[i], options, &reader, nullptr));
        for (uint64_t offset = 0; offset < size; offset += kChunk) read(*reader, buffer.get(), offset, kChunk);
      }
      std::cout << "RR后全部160MiB字节比对通过；校验阶段RR关闭，校验读不计入准备负载" << std::endl;
    }
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "受控测试失败：" << error.what() << std::endl;
    return 1;
  }
}
