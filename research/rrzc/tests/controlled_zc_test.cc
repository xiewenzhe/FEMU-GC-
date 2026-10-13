// 隔离客机专用：四格受控场景，真实写满/删除后观察原 ZC 与后续 RR。
// .sst 是文件系统模式载荷，不是 RocksDB SST；不格式化，不直接调用 GC/RR。
// 原模式分阶段启停 RR；concurrent 模式同进程开启 RR/ZC，但不是性能对照。
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include "logging/logging.h"
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "plugin/zenfs/fs/snapshot.h"

using namespace rocksdb;
constexpr uint64_t kMiB = 1 << 20;
constexpr uint64_t kZoneBytes = 256 * kMiB;
constexpr uint64_t kThreshold = 4096;
constexpr uint64_t kBaseReads = 8192;
constexpr unsigned kFillers = 22;
constexpr uint64_t kValid[] = {32 * kMiB, 32 * kMiB, 128 * kMiB, 128 * kMiB};
static void require(bool value, const std::string& message) {
  if (!value) throw std::runtime_error(message);
}
template <typename S> static void check(const S& status) { require(status.ok(), status.ToString()); }
static std::string path(unsigned index, bool dead = false) {
  return "/controlled-zc/case" + std::to_string(index) + (dead ? ".dead.bin" : ".live.sst");
}
static unsigned char pattern(uint64_t offset, unsigned seed) {
  return ((offset % kMiB) * 131 + 17 + seed * 31) % 251;
}
static std::optional<ZoneExtentSnapshot> find_extent(ZenFS& fs, const std::string& name) {
  ZenFSSnapshot snapshot;
  ZenFSSnapshotOptions options;
  options.zone_ = options.zone_file_ = true;
  fs.GetZenFSSnapshot(snapshot, options);
  std::optional<ZoneExtentSnapshot> result;
  for (const auto& item : snapshot.extents_) if (item.filename == name) {
    require(!result, "测试文件出现多片段映射");
    result = item;
  }
  return result;
}
static ZoneExtentSnapshot extent(ZenFS& fs, const std::string& name, uint64_t size) {
  auto result = find_extent(fs, name);
  require(result && result->length == size, "测试文件映射缺失或长度不符");
  return *result;
}
static ZoneReadPressure pressure(ZonedBlockDevice& device, uint64_t start) {
  ZoneReadPressureReport report;
  check(device.GetZoneReadPressure(&report));
  require(report.read_threshold == kThreshold && report.blocks_per_zone == 64 && report.zones.size() == 32,
          "设备不是本测试要求的 4096/32区/64块配置");
  for (const auto& item : report.zones) if (item.start == start) return item;
  throw std::runtime_error("C1h 没有目标 Zone");
}
static void fill_buffer(char* buffer, unsigned seed) {
  for (uint64_t i = 0; i < kMiB; ++i) buffer[i] = pattern(i, seed);
}
static void write(ZenFS& fs, const std::string& name, uint64_t size, unsigned seed,
                  char* buffer, const FileOptions& options,
                  Env::WriteLifeTimeHint hint = Env::WLTH_MEDIUM) {
  require(!fs.FileExists(name, IOOptions(), nullptr).ok(), "文件已存在，拒绝覆盖");
  fill_buffer(buffer, seed);
  std::unique_ptr<FSWritableFile> writer;
  check(fs.NewWritableFile(name, options, &writer, nullptr));
  writer->SetWriteLifeTimeHint(hint);
  for (uint64_t offset = 0; offset < size; offset += kMiB)
    check(writer->Append(Slice(buffer, kMiB), IOOptions(), nullptr));
  check(writer->Close(IOOptions(), nullptr));
}
static void read(FSRandomAccessFile& file, char* scratch, const char* expected,
                 uint64_t offset, size_t size) {
  Slice result;
  check(file.Read(offset, size, IOOptions(), &result, scratch, nullptr));
  require(result.size() == size, "真实读取发生短读");
  require(std::memcmp(result.data(), expected + offset % kMiB, size) == 0, "载荷字节不符");
}
static void hot_reads(ZenFS& fs, unsigned index, uint64_t count, char* scratch,
                      char* expected, const FileOptions& options) {
  fill_buffer(expected, index);
  std::unique_ptr<FSRandomAccessFile> reader;
  check(fs.NewRandomAccessFile(path(index), options, &reader, nullptr));
  for (uint64_t i = 0; i < count; ++i)
    read(*reader, scratch, expected, index % 2 ? (i % 8) * 65536 : 0, 4096);
}
static void verify(ZenFS& fs, unsigned count, char* scratch, char* expected,
                   const FileOptions& options) {
  for (unsigned index = 0; index < count; ++index) {
    uint64_t size = 0;
    check(fs.GetFileSize(path(index), IOOptions(), &size, nullptr));
    require(size == kValid[index], "有效文件长度改变");
    fill_buffer(expected, index);
    std::unique_ptr<FSRandomAccessFile> reader;
    check(fs.NewRandomAccessFile(path(index), options, &reader, nullptr));
    for (uint64_t offset = 0; offset < size; offset += kMiB)
      read(*reader, scratch, expected, offset, kMiB);
  }
}
static uint64_t free_percent(ZonedBlockDevice& device) {
  const uint64_t free = device.GetFreeSpace();
  return 100 * free / (free + device.GetUsedSpace() + device.GetReclaimableSpace());
}
static void wait_for_move(ZenFS& fs, ZonedBlockDevice& device, unsigned index,
                          uint64_t source) {
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(60);
  while (std::chrono::steady_clock::now() < deadline) {
    auto current = find_extent(fs, path(index));
    // 写锁期间快照可暂时缺失，不视为数据丢失，也不提前宣布维护完成。
    if (current && current->zone_start != source && device.GetIOZone(source)->IsEmpty()) {
      require(current->length == kValid[index] && pressure(device, source).sum_block_reads == 0,
              "维护后的长度或源区清零不符");
      return;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  throw std::runtime_error("等待实际迁移和源重置超时");
}
static std::string expire_path(unsigned index) {
  return "/controlled-zc/case" + std::to_string(index) + ".expire.sst";
}
static uint64_t survivor_size(unsigned index) {
  return (index < 2 ? 32 : 64) * kMiB;
}
static void verify_survivors(ZenFS& fs, unsigned first, unsigned count,
                             char* scratch, char* expected, const FileOptions& options) {
  for (unsigned i = first; i < first + count; ++i) {
    uint64_t size = 0;
    check(fs.GetFileSize(path(i), IOOptions(), &size, nullptr));
    require(size == survivor_size(i), "失效实验剩余文件长度改变");
    if (i >= 2) require(!fs.FileExists(expire_path(i), IOOptions(), nullptr).ok(), "失效文件仍存在");
    fill_buffer(expected, i);
    std::unique_ptr<FSRandomAccessFile> reader;
    check(fs.NewRandomAccessFile(path(i), options, &reader, nullptr));
    for (uint64_t offset = 0; offset < size; offset += kMiB)
      read(*reader, scratch, expected, offset, kMiB);
  }
}
static void wait_survivor_move(ZenFS& fs, ZonedBlockDevice& device, unsigned index,
                               uint64_t source) {
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(60);
  while (std::chrono::steady_clock::now() < deadline) {
    auto current = find_extent(fs, path(index));
    if (current && current->zone_start != source && device.GetIOZone(source)->IsEmpty()) {
      require(current->length == survivor_size(index) && pressure(device, source).sum_block_reads == 0,
              "失效实验迁移后源状态不符");
      return;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  throw std::runtime_error("失效实验等待实际维护超时");
}
static void observe_future(ZenFS& fs, ZonedBlockDevice& device, const std::shared_ptr<Logger>& logger,
                            const std::string& mode, const std::string& out, unsigned first,
                            unsigned end, char* scratch, const FileOptions& options) {
  const bool small = mode == "future-smoke" || mode == "future-equal-smoke";
  const bool equal_fast = mode == "future-equal-fast" || mode == "future-equal-smoke";
  // 相同计划总量768次，分别24秒和3秒发送；实际完成量仍单独记录。
  const unsigned rate = mode == "future-smoke" ? 64 : mode == "future-low" ? 8
                        : mode == "future-medium" || mode == "future-equal-slow" ? 32 : 256;
  const unsigned read_seconds = equal_fast ? 3 : small ? 8 : 24, window_seconds = small ? 20 : 40;
  std::unique_ptr<FSRandomAccessFile> readers[4];
  std::unique_ptr<char, decltype(&std::free)> patterns[4] = {
      {nullptr, &std::free}, {nullptr, &std::free}, {nullptr, &std::free}, {nullptr, &std::free}};
  for (unsigned i = first; i < end; ++i) {
    void* raw = nullptr;
    require(posix_memalign(&raw, 4096, kMiB) == 0, "申请定速读取模式缓冲失败");
    patterns[i].reset(static_cast<char*>(raw)); fill_buffer(patterns[i].get(), i);
    check(fs.NewRandomAccessFile(path(i), options, &readers[i], nullptr));
  }
  std::ofstream config(out + "/window.csv"), samples(out + "/pressure.csv"), counts(out + "/read-counts.csv");
  require(config.good() && samples.good() && counts.good(), "无法创建未来读取记录");
  config << "每文件目标读每秒,业务读取秒数,观察窗口秒数,采样间隔毫秒\n"
         << rate << ',' << read_seconds << ',' << window_seconds << ",100\n";
  config.close();
  samples << "窗口毫秒,Zone,主机代际前,主机代际后,观察序号前,观察序号后,重置窗稳定,最大读数,读数总和,80%风险块,90%风险块,达阈值块,首次采样跨阈值\n";
  counts << "窗口毫秒,组号,累计业务4KiB读次数\n";
  const auto start = std::chrono::steady_clock::now();
  const auto deadline = start + std::chrono::seconds(window_seconds);
  const auto read_deadline = start + std::chrono::seconds(read_seconds);
  auto next_sample = start;
  uint64_t completed[4] = {}, tick = 0;
  bool finished_reads = false;
  std::set<std::pair<uint64_t, uint64_t>> crossed;
  Info(logger, "未来读取观察：开始，每文件目标读每秒=%u，业务读取秒数=%u，观察窗口秒数=%u，无补读写锁",
       rate, read_seconds, window_seconds);
  while (std::chrono::steady_clock::now() < deadline) {
    auto now = std::chrono::steady_clock::now();
    if (now < read_deadline && tick < uint64_t(rate) * read_seconds) {
      const auto scheduled = start + std::chrono::nanoseconds(tick * 1000000000ULL / rate);
      if (now >= scheduled) {
        for (unsigned i = first; i < end; ++i) {
          read(*readers[i], scratch, patterns[i].get(), i % 2 ? (completed[i] % 8) * 65536 : 0, 4096);
          ++completed[i];
        }
        ++tick;
      }
    } else if (!finished_reads) {
      // 只记录实际完成量，不能把目标速率当作真实吞吐；读取迟滞可能使次数不足。
      finished_reads = true;
      Info(logger, "未来读取观察：业务读取结束，实际轮次=%lu，目标轮次=%u，继续观察维护",
           tick, rate * read_seconds);
    }
    now = std::chrono::steady_clock::now();
    if (now >= next_sample) {
      const auto before = device.GetZoneResetStates();
      ZoneReadPressureReport report;
      check(device.GetZoneReadPressure(&report));
      const auto after = device.GetZoneResetStates();
      require(report.read_threshold == kThreshold && report.blocks_per_zone == 64 && report.zones.size() == 32,
              "未来读取设备参数变化");
      const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - start).count();
      for (const auto& p : report.zones) {
        if (!before.count(p.start) || !after.count(p.start)) continue;
        const auto& a = before.at(p.start); const auto& b = after.at(p.start);
        const bool stable = a.known && b.known && a.stable && b.stable
                            && a.generation == b.generation && a.sequence == b.sequence;
        const bool first_cross = stable && p.blocks_at_threshold > 0 && crossed.emplace(p.start, b.generation).second;
        samples << ms << ',' << p.start << ',' << a.generation << ',' << b.generation << ',' << a.sequence << ','
                << b.sequence << ',' << stable << ',' << p.max_block_reads << ',' << p.sum_block_reads << ','
                << p.blocks_ge_80pct << ',' << p.blocks_ge_90pct << ',' << p.blocks_at_threshold << ',' << first_cross << '\n';
        if (first_cross) Info(logger, "未来读取观察：首次采样跨阈值，Zone=%lu，主机代际=%lu，窗口毫秒=%ld，最大读数=%lu，达阈值块=%u",
                              p.start, b.generation, ms, p.max_block_reads, p.blocks_at_threshold);
      }
      for (unsigned i = first; i < end; ++i) counts << ms << ',' << i << ',' << completed[i] << '\n';
      next_sample = now + std::chrono::milliseconds(100);
    }
    // 控制发送计划而非改变设备延迟；不用于系统性能比较。
    auto wake = next_sample;
    if (!finished_reads && tick < uint64_t(rate) * read_seconds)
      wake = std::min(wake, start + std::chrono::nanoseconds(tick * 1000000000ULL / rate));
    if (wake > std::chrono::steady_clock::now()) std::this_thread::sleep_until(std::min(wake, deadline));
  }
  const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start).count();
  for (unsigned i = first; i < end; ++i) counts << elapsed << ',' << i << ',' << completed[i] << '\n';
  samples.close(); counts.close();
  require(samples.good() && counts.good(), "未来读取CSV写入失败");
  Info(logger, "未来读取观察：窗口关闭请求，实际窗口毫秒=%ld，之后等待已在执行的维护结束", elapsed);
  std::cout << "未来读取窗口完成：每文件目标" << rate << "读/秒，实际每文件" << tick
            << "次，业务读取" << read_seconds << "秒，观察" << window_seconds << "秒" << std::endl;
}
static void run_near(ZenFS& fs, ZonedBlockDevice& device, const std::shared_ptr<Logger>& logger,
                      const std::string& mode, const std::string& out, char* scratch,
                      char* expected, const FileOptions& options) {
  if (mode == "near-verify") {
    verify_survivors(fs, 0, 4, scratch, expected, options);
    std::cout << "剩余四组192MiB字节比对通过；校验阶段RR关闭" << std::endl;
    return;
  }
  const bool future_mode = mode.rfind("future-", 0) == 0;
  const bool small = mode == "near-smoke" || mode == "future-smoke" || mode == "future-equal-smoke", before = mode != "near-after";
  const unsigned first = small ? 2 : 0, end = small ? 3 : 4;
  require(device.GetUsedSpace() == 0 && !std::filesystem::exists(out + "/sources.tsv"), "拒绝混入旧载荷或覆盖记录");
  check(fs.CreateDir("/controlled-zc", IOOptions(), nullptr));
  if (!small) for (unsigned i = 0; i < kFillers; ++i) {
    write(fs, "/controlled-zc/filler" + std::to_string(i) + ".bin", kZoneBytes, i + 10, expected, options);
    if (i % 4 == 3) std::cout << "近阈值填充进展：" << i + 1 << "/22个完整Zone" << std::endl;
  }
  uint64_t sources[4] = {};
  std::unique_ptr<FSWritableFile> guards[4], expire_guards[4];
  for (unsigned i = first; i < end; ++i) {
    write(fs, path(i), survivor_size(i), i, expected, options, Env::WLTH_LONG);
    const auto live = extent(fs, path(i), survivor_size(i));
    sources[i] = live.zone_start;
    require(live.start == sources[i], "剩余文件不是源区起点");
    check(fs.ReopenWritableFile(path(i), options, &guards[i], nullptr));
    if (i >= 2) {
      write(fs, expire_path(i), 64 * kMiB, i + 20, expected, options);
      const auto expiring = extent(fs, expire_path(i), 64 * kMiB);
      require(expiring.zone_start == sources[i] && expiring.start == sources[i] + 64 * kMiB,
              "待失效SST未与剩余文件共享源区");
      check(fs.ReopenWritableFile(expire_path(i), options, &expire_guards[i], nullptr));
    }
    write(fs, path(i, true), kZoneBytes - kValid[i], i + 4, expected, options);
    const auto dead = extent(fs, path(i, true), kZoneBytes - kValid[i]);
    require(dead.zone_start == sources[i] && dead.start == sources[i] + kValid[i], "无效载荷未连续共享源区");
    Zone* zone = device.GetIOZone(sources[i]);
    require(zone->IsFull() && zone->used_capacity_ == kZoneBytes && pressure(device, sources[i]).sum_block_reads == 0,
            "源区未真实写满或初始读数非零");
    hot_reads(fs, i, 3584, scratch, expected, options);
  }
  std::ofstream manifest(out + "/sources.tsv"), csv(out + "/initial.csv");
  require(manifest.good() && csv.good(), "无法创建近阈值初始记录");
  csv << "组号,读取分布,Zone字节地址,有效字节,真实删除字节,累计4KiB读次数,最大读数,读数总和,达阈值块,空闲比例\n";
  for (unsigned i = first; i < end; ++i) {
    check(fs.DeleteFile(path(i, true), IOOptions(), nullptr));
    const auto p = pressure(device, sources[i]);
    require(device.GetIOZone(sources[i])->used_capacity_ == kValid[i]
            && device.GetIOZone(sources[i])->GetResetState().generation == 0
            && p.sum_block_reads == 3584 && p.max_block_reads == (i % 2 ? 448 : 3584)
            && p.blocks_at_threshold == 0, "初始近阈值分布或有效量不符");
    manifest << sources[i] << '\t' << kValid[i] << '\n';
    csv << i << ',' << (i % 2 ? "八块分散" : "单块集中") << ',' << sources[i] << ',' << kValid[i]
        << ',' << kZoneBytes - kValid[i] << ",3584," << p.max_block_reads << ',' << p.sum_block_reads
        << ",0," << free_percent(device) << '\n';
  }
  manifest.close(); csv.close();
  require(small || free_percent(device) == 10, "近阈值场景未形成10%初始空闲");
  Info(logger, "受控近阈值：初始3584次读记录完成，即将释放准备锁，RR/ZC始终开启");
  for (unsigned i = first; i < end; ++i) {
    check(guards[i]->Close(IOOptions(), nullptr)); guards[i].reset();
    if (expire_guards[i]) { check(expire_guards[i]->Close(IOOptions(), nullptr)); expire_guards[i].reset(); }
  }
  if (!small) {
    for (unsigned i = 0; i < 2; ++i) wait_survivor_move(fs, device, i, sources[i]);
    for (unsigned i = 2; i < 4; ++i)
      require(extent(fs, path(i), survivor_size(i)).zone_start == sources[i]
              && pressure(device, sources[i]).sum_block_reads == 3584, "未来读前大组已被维护或读数改变");
    Info(logger, "受控近阈值：两个小组已由真实维护迁移，两个大组仍在原区且未达阈值");
  }
  auto invalidate = [&]() {
    for (unsigned i = 2; i < end; ++i) {
      const auto expiring = extent(fs, expire_path(i), 64 * kMiB);
      const auto live = find_extent(fs, path(i));
      // 持有剩余文件写锁时快照跳过它；删除的过期文件没有未释放的写句柄。
      if (live) require(live->zone_start == expiring.zone_start, "失效前两份SST不在同一区");
      check(fs.DeleteFile(expire_path(i), IOOptions(), nullptr));
      require(!fs.FileExists(expire_path(i), IOOptions(), nullptr).ok(), "真实失效删除未生效");
    }
    Info(logger, "受控近阈值：真实失效删除完成，时序=%s，每个大组删除64MiB",
         before ? "RR前" : "RR后");
  };
  uint64_t read_zones[4] = {};
  for (unsigned i = first; i < end; ++i) read_zones[i] = extent(fs, path(i), survivor_size(i)).zone_start;
  // 补读同步仅锁定大组的剩余文件；不会关闭RR或改变GC线程。
  for (unsigned i = 2; i < end; ++i) check(fs.ReopenWritableFile(path(i), options, &guards[i], nullptr));
  if (before) invalidate();
  if (future_mode) {
    for (unsigned i = 2; i < end; ++i) { check(guards[i]->Close(IOOptions(), nullptr)); guards[i].reset(); }
    observe_future(fs, device, logger, mode, out, first, end, scratch, options);
    if (small) {
      require(device.GetIOZone(sources[2])->GetResetState().generation == 1, "未来读取短测未观察到实际源Reset");
      verify_survivors(fs, 2, 1, scratch, expected, options);
      std::cout << "未来读取短测：剩余64MiB字节比对通过" << std::endl;
    }
    return;
  }
  std::ofstream future(out + "/future.csv");
  require(future.good(), "无法创建补读观测");
  future << "组号,读取Zone,新增4KiB读次数,读数总和,最大读数,达阈值块,区有效字节\n";
  for (unsigned i = first; i < end; ++i) {
    hot_reads(fs, i, 512, scratch, expected, options);
    const auto p = pressure(device, read_zones[i]);
    // 小组已经迁往共享目标区，C1区汇总不能当作单文件读数。
    if (i >= 2) require(p.sum_block_reads == 4096 && p.max_block_reads == (i % 2 ? 512 : 4096)
                        && p.blocks_at_threshold == (i % 2 ? 0 : 1), "大组固定补读状态不符");
    else require(p.blocks_at_threshold == 0, "小组迁移后固定补读已达RR阈值");
    const auto valid = device.GetIOZone(read_zones[i])->used_capacity_.load();
    if (i >= 2) require(valid == (before ? 64 : 128) * kMiB, "补读时失效时序对应有效量不符");
    future << i << ',' << read_zones[i] << ",512," << p.sum_block_reads << ',' << p.max_block_reads
           << ',' << p.blocks_at_threshold << ',' << valid << '\n';
  }
  future.close();
  Info(logger, "受控近阈值：各组固定补读512次完成，即将释放补读锁");
  for (unsigned i = 2; i < end; ++i) { check(guards[i]->Close(IOOptions(), nullptr)); guards[i].reset(); }
  wait_survivor_move(fs, device, 2, sources[2]);
  Info(logger, "受控近阈值：集中大组RR和源重置已观察完成");
  if (!before) invalidate();
  if (small) {
    verify_survivors(fs, 2, 1, scratch, expected, options);
    std::cout << "近阈值短测通过：3584加512读跨阈值、真实删除64MiB、RR后剩余64MiB字节比对通过" << std::endl;
  } else {
    // 有限追加窗口：只观察原线程，不把未见维护解释为永远不会发生。
    std::this_thread::sleep_for(std::chrono::seconds(12));
    require(extent(fs, path(3), survivor_size(3)).zone_start == sources[3]
            && device.GetIOZone(sources[3])->used_capacity_ == 64 * kMiB
            && device.GetIOZone(sources[3])->GetResetState().generation == 0
            && pressure(device, sources[3]).blocks_at_threshold == 0, "低风险大组发生未预期维护");
    Info(logger, "受控近阈值：追加12秒观察结束，分散大组仍在原区，结束维护观察");
    std::cout << "近阈值观察通过：初始每组3584读、未来每组512读、每个大组真实失效64MiB；时序="
              << (before ? "RR前" : "RR后") << std::endl;
  }
}
static void train_choice_trends(ZenFS& fs, const std::shared_ptr<Logger>& logger,
                                const std::string& out, char* scratch, const FileOptions& options) {
  // 真实的决策前读取；采样器只能看到已发生的读数，不接收未来发送计划。
  std::unique_ptr<FSRandomAccessFile> readers[2];
  std::vector<char> patterns[2] = {std::vector<char>(kMiB), std::vector<char>(kMiB)};
  for (unsigned i = 0; i < 2; ++i) {
    fill_buffer(patterns[i].data(), i+2);
    check(fs.NewRandomAccessFile(path(i+2), options, &readers[i], nullptr));
  }
  std::ofstream counts(out + "/training-counts.csv");
  require(counts.good(), "无法创建决策前实际读取记录");
  counts << "组号,实际业务4KiB读次数,计划读每秒,发送秒数\n";
  Info(logger, "自动选区准备：开始决策前真实读取，每文件32次每秒共768次，持准备锁，非未来信息注入");
  const auto start = std::chrono::steady_clock::now();
  for (uint64_t tick = 0; tick < 768; ++tick) {
    std::this_thread::sleep_until(start + std::chrono::nanoseconds(tick*1000000000ULL/32));
    for (unsigned i = 0; i < 2; ++i)
      read(*readers[i], scratch, patterns[i].data(), i ? (tick%8)*65536 : 0, 4096);
  }
  for (unsigned i = 2; i < 4; ++i) counts << i << ",768,32,24\n";
  counts.close(); require(counts.good(), "决策前读取记录失败");
  Info(logger, "自动选区准备：决策前真实读取完成，两文件各768次，即将释放准备锁");
}

static void run_choice(ZenFS& fs, ZonedBlockDevice& device, const std::shared_ptr<Logger>& logger,
                       const std::string& mode, const std::string& out, char* scratch,
                       char* expected, const FileOptions& options) {
  const bool simple = mode.rfind("choice-weighted-", 0) == 0;
  const bool automatic = simple || mode.rfind("choice-auto-", 0) == 0;
  const bool expiry = !simple && (automatic || mode.rfind("choice-expire-", 0) == 0);
  if (mode == "choice-verify" || mode == "choice-expire-verify" || mode == "choice-auto-verify" || mode == "choice-weighted-verify") {
    verify_survivors(fs, 2, expiry ? 1 : 2, scratch, expected, options);
    if (expiry) {
      require(fs.FileExists(path(3), IOOptions(), nullptr).IsNotFound(), "计划失效冷文件仍存在或查询失败");
      std::cout << "选区失效实验剩余64MiB字节比对通过，冷文件不存在；校验阶段RR关闭" << std::endl;
    } else std::cout << "选区实验剩余128MiB字节比对通过；校验阶段RR关闭" << std::endl;
    return;
  }
  const bool cold = mode == "choice-cold" || mode == "choice-expire-cold" || mode == "choice-auto-space" || mode == "choice-weighted-valid";
  const bool small = mode == "choice-smoke" || mode == "choice-expire-smoke" || mode == "choice-auto-smoke" || mode == "choice-weighted-smoke";
  uint64_t requested = 0;
  if (automatic) {
    require(!getenv("ZENFS_ZC_TEST_SOURCE") && !getenv("ZENFS_ZC_TEST_ENABLE"), "自动机制禁止注入人工源区");
    if (simple) {
      require(!getenv("ZENFS_RRZC_MODE"), "简单双因素禁止启用趋势成本模式");
      require(getenv("ZENFS_RRZC_SIMPLE_MODE") && std::string(getenv("ZENFS_RRZC_SIMPLE_MODE")) == (cold ? "valid" : "weighted"),
              "简单双因素模式与阶段不符");
    } else
      require(getenv("ZENFS_RRZC_MODE") && std::string(getenv("ZENFS_RRZC_MODE")) == (cold ? "space" : "rr"),
              "自动选区模式与阶段不符");
    requested = cold ? 7516192768ULL : 7784628224ULL;
  } else {
    require(getenv("ZENFS_ZC_TEST_ENABLE") && std::string(getenv("ZENFS_ZC_TEST_ENABLE")) == "1"
            && getenv("ZENFS_ZC_TEST_SOURCE"), "选区实验须显式启用一次性指定源区");
    requested = std::stoull(getenv("ZENFS_ZC_TEST_SOURCE"));
  }
  require(device.GetUsedSpace() == 0 && !std::filesystem::exists(out + "/choice-initial.csv"), "拒绝混入旧选区载荷");
  if (expiry) {
    std::ofstream plan(out + "/expiry-plan.csv");
    require(plan.good(), "无法创建预先确定的失效计划");
    plan << "失效组号,失效字节,失效阶段,每文件未来业务读次数,追加写入字节\n"
         << "3," << 64 * kMiB << ",未来窗口后且追加写入前,768," << 512 * kMiB << '\n';
    plan.close(); require(plan.good(), "失效计划写入失败");
  }
  check(fs.CreateDir("/controlled-zc", IOOptions(), nullptr));
  for (unsigned i = 0; i < 24; ++i) {
    write(fs, "/controlled-zc/filler" + std::to_string(i) + ".bin", kZoneBytes, i + 10, expected, options);
    if (i % 8 == 7) std::cout << "选区填充进展：" << i + 1 << "/24个完整Zone" << std::endl;
  }
  uint64_t sources[4] = {};
  std::unique_ptr<FSWritableFile> guards[4];
  // 先写冷区再写热区；二者空间信息相同，冷区为地址顺序首选参考。
  for (unsigned i : {3U, 2U}) {
    write(fs, path(i), 64 * kMiB, i, expected, options, Env::WLTH_LONG);
    auto live = extent(fs, path(i), 64 * kMiB);
    sources[i] = live.zone_start;
    require(live.start == sources[i], "选区有效文件不在区起点");
    check(fs.ReopenWritableFile(path(i), options, &guards[i], nullptr));
    write(fs, path(i, true), 192 * kMiB, i + 4, expected, options);
    auto dead = extent(fs, path(i, true), 192 * kMiB);
    require(dead.zone_start == sources[i] && dead.start == sources[i] + 64 * kMiB
            && device.GetIOZone(sources[i])->IsFull() && pressure(device, sources[i]).sum_block_reads == 0,
            "选区源未真实写满或映射不符");
    hot_reads(fs, i, automatic && !simple ? 2816 : 3584, scratch, expected, options);
  }
  require(sources[3] == 7516192768ULL && sources[2] == 7784628224ULL
          && requested == sources[cold ? 3 : 2], "实际源区与显式实验配置不一致");
  std::ofstream initial(out + "/choice-initial.csv"), states(out + "/choice-states.csv");
  require(initial.good() && states.good(), "无法创建选区记录");
  initial << "组号,Zone字节地址,有效字节,无效字节,最大读数,读数总和,达阈值块,空闲比例\n";
  if (automatic && !simple) {
    for (unsigned i : {2U, 3U}) check(fs.DeleteFile(path(i, true), IOOptions(), nullptr));
    train_choice_trends(fs, logger, out, scratch, options);
    require(device.GetIOZone(sources[2])->GetResetState().generation == 0 &&
            device.GetIOZone(sources[3])->GetResetState().generation == 0,
            "准备锁期间自动机制发生源Reset");
  }
  for (unsigned i : {2U, 3U}) {
    if (!automatic || simple) check(fs.DeleteFile(path(i, true), IOOptions(), nullptr));
    auto p = pressure(device, sources[i]);
    require(device.GetIOZone(sources[i])->used_capacity_ == 64 * kMiB
            && p.sum_block_reads == 3584 && p.max_block_reads == (i == 2 ? 3584 : 448)
            && p.blocks_at_threshold == 0, "选区初始风险或有效量不符");
    initial << i << ',' << sources[i] << ',' << 64 * kMiB << ',' << 192 * kMiB << ','
            << p.max_block_reads << ',' << p.sum_block_reads << ",0," << free_percent(device) << '\n';
  }
  initial.close();
  states << "阶段,空闲字节,有效字节,满区可回收字节,空闲比例,热源代际,冷源代际,热文件Zone,冷文件Zone\n";
  auto record = [&](const char* stage) {
    states << stage << ',' << device.GetFreeSpace() << ',' << device.GetUsedSpace() << ','
           << device.GetReclaimableSpace() << ',' << free_percent(device) << ','
           << device.GetIOZone(sources[2])->GetResetState().generation << ','
           << device.GetIOZone(sources[3])->GetResetState().generation << ','
           << extent(fs, path(2), 64 * kMiB).zone_start << ','
           << (expiry && fs.FileExists(path(3), IOOptions(), nullptr).IsNotFound()
               ? 0 : extent(fs, path(3), 64 * kMiB).zone_start) << '\n';
    states.flush(); require(states.good(), "选区状态记录失败");
  };
  // 准备锁持有时快照跳过有效文件，不能从此处取文件映射状态。
  require(free_percent(device) == 10, "选区实验初始空闲不是10%");
  if (small && (!automatic || simple)) {
    Info(logger, "受控等预算选区：短测持准备锁12秒，验证完整覆盖前不消耗一次性机会");
    std::this_thread::sleep_for(std::chrono::seconds(12));
    require(device.GetIOZone(sources[2])->GetResetState().generation == 0
            && device.GetIOZone(sources[3])->GetResetState().generation == 0,
            "准备锁期间发生源Reset");
  }
  if (automatic)
    Info(logger, "受控等预算选区：初始记录完成，冷热区各有效64MiB/无效192MiB，预期首区=%lu仅核验，不传入选择器，即将释放准备锁", requested);
  else
    Info(logger, "受控等预算选区：初始记录完成，冷热区各有效64MiB/无效192MiB，指定Zone=%lu，即将释放准备锁", requested);
  for (unsigned i : {2U, 3U}) { check(guards[i]->Close(IOOptions(), nullptr)); guards[i].reset(); }
  wait_survivor_move(fs, device, cold ? 3 : 2, requested);
  require(device.GetIOZone(sources[cold ? 2 : 3])->GetResetState().generation == 0
          && extent(fs, path(cold ? 2 : 3), 64 * kMiB).zone_start == sources[cold ? 2 : 3],
          "初始单区选择没有留下另一候选");
  record("初始ZC后");
  Info(logger, "受控等预算选区：初始单区ZC完成，开始相同未来业务读取");
  observe_future(fs, device, logger, small ? "future-equal-smoke" : "future-equal-slow", out, 2, 4, scratch, options);
  require(device.GetIOZone(sources[2])->GetResetState().generation == 1
          && device.GetIOZone(sources[3])->GetResetState().generation == (cold ? 1 : 0),
          "未来窗口维护状态不符");
  record("未来窗口后");
  if (expiry) {
    Info(logger, "受控等预算选区：冷文件计划失效开始，组号=3，失效字节=%lu，边界=未来窗口后且追加写入前", 64 * kMiB);
    check(fs.DeleteFile(path(3), IOOptions(), nullptr));
    require(fs.FileExists(path(3), IOOptions(), nullptr).IsNotFound(), "冷文件真实删除未生效或查询失败");
    Info(logger, "受控等预算选区：冷文件计划失效完成，组号=3，失效字节=%lu", 64 * kMiB);
    record("冷文件失效后");
  }
  // 两组窗口后空间可能不同；相同新增写入使余下冷区真实ZC，核对终态账目。
  Info(logger, "受控等预算选区：空间补齐开始，相同新增512MiB写入，不追加未来读取");
  for (unsigned i = 0; i < 2; ++i)
    write(fs, "/controlled-zc/balance" + std::to_string(i) + ".bin", kZoneBytes, i + 40,
          expected, options, Env::WLTH_LONG);
  if (!cold && !expiry) wait_survivor_move(fs, device, 3, sources[3]);
  require(device.GetIOZone(sources[2])->GetResetState().generation == 1
          && device.GetIOZone(sources[3])->GetResetState().generation == 1, "空间补齐后两源未各成功Reset一次");
  record("相同追加写入并补齐后");
  states.close();
  Info(logger, "受控等预算选区：空间补齐结束，后续是否净节省必须按完整维护账目判断");
  std::cout << "等预算选区完成：冷热各64MiB有效/192MiB无效，每文件未来768次读，相同追加512MiB写入" << std::endl;
  if (small) {
    verify_survivors(fs, 2, expiry ? 1 : 2, scratch, expected, options);
    if (expiry) {
      require(fs.FileExists(path(3), IOOptions(), nullptr).IsNotFound(), "短测失效冷文件仍存在或查询失败");
      std::cout << "选区失效短测剩余64MiB字节比对通过，冷文件不存在" << std::endl;
    } else std::cout << "选区短测剩余128MiB字节比对通过" << std::endl;
  }
}
int main(int argc, char** argv) {
  try {
    require(argc == 3, "用法：程序 choice-expire-smoke|choice-expire-cold|choice-expire-hot|choice-expire-verify|choice-smoke|choice-cold|choice-hot|choice-verify|future-equal-smoke|future-equal-slow|future-equal-fast|future-smoke|future-low|future-medium|future-high|near-verify|smoke|concurrent-smoke|concurrent|near-smoke|near-before|near-after|zc|rr-first|future|rr-second|verify /tmp/rrzc-独立目录");
    const std::string mode = argv[1], out = argv[2];
    const bool choice = mode == "choice-smoke" || mode == "choice-cold" || mode == "choice-hot" || mode == "choice-verify"
                        || mode == "choice-expire-smoke" || mode == "choice-expire-cold" || mode == "choice-expire-hot" || mode == "choice-expire-verify"
                        || mode == "choice-auto-smoke" || mode == "choice-auto-space" || mode == "choice-auto-rr" || mode == "choice-auto-verify"
                        || mode == "choice-weighted-smoke" || mode == "choice-weighted-valid" || mode == "choice-weighted-rr" || mode == "choice-weighted-verify";
    const bool future = mode == "future-smoke" || mode == "future-low" || mode == "future-medium" || mode == "future-high"
                        || mode == "future-equal-smoke" || mode == "future-equal-slow" || mode == "future-equal-fast";
    const bool near = future || mode == "near-smoke" || mode == "near-before" || mode == "near-after" || mode == "near-verify";
    const bool concurrent = mode == "concurrent" || mode == "concurrent-smoke";
    const bool short_test = mode == "smoke" || mode == "concurrent-smoke";
    require(choice || near || concurrent || mode == "smoke" || mode == "zc" || mode == "rr-first" || mode == "future" || mode == "rr-second" || mode == "verify",
            "模式不支持");
    const bool rr = (choice && mode != "choice-verify" && mode != "choice-expire-verify" && mode != "choice-auto-verify" && mode != "choice-weighted-verify") || (near && mode != "near-verify") || concurrent || mode == "rr-first" || mode == "rr-second";
    require(getenv("ZENFS_RR_ENABLE") && std::string(getenv("ZENFS_RR_ENABLE")) == (rr ? "1" : "0"), "RR 开关与阶段不符");
    require(getenv("ZENFS_MIGRATE_RESERVE") && std::string(getenv("ZENFS_MIGRATE_RESERVE")) == "1", "须使用已有迁移预留版本");
    require(out.rfind("/tmp/rrzc-", 0) == 0 && std::filesystem::is_directory(out), "须提供隔离日志目录");
    const std::string logfile = out + "/zenfs." + mode + ".log";
    require(!std::filesystem::exists(logfile), "阶段日志已存在，拒绝覆盖");
    std::shared_ptr<Logger> logger;
    check(Env::Default()->NewLogger(logfile, &logger));
    logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned = std::make_unique<ZonedBlockDevice>("nvme0n1", ZbdBackendType::kBlockDev, logger);
    check(owned->Open(false, true));
    auto* device = owned.get();
    auto fs = std::make_unique<ZenFS>(owned.release(), FileSystem::Default(), logger);
    check(fs->Mount(false));
    void *scratch_raw = nullptr, *expected_raw = nullptr;
    require(posix_memalign(&scratch_raw, 4096, kMiB) == 0, "申请读缓冲失败");
    std::unique_ptr<char, decltype(&std::free)> scratch(static_cast<char*>(scratch_raw), &std::free);
    require(posix_memalign(&expected_raw, 4096, kMiB) == 0, "申请模式缓冲失败");
    std::unique_ptr<char, decltype(&std::free)> expected(static_cast<char*>(expected_raw), &std::free);
    FileOptions options;
    options.use_direct_reads = options.use_direct_writes = true;
    if (choice) {
      run_choice(*fs, *device, logger, mode, out, scratch.get(), expected.get(), options);
      return 0;
    }
    if (near) {
      run_near(*fs, *device, logger, mode, out, scratch.get(), expected.get(), options);
      return 0;
    }
    uint64_t sources[4] = {};
    if (mode == "smoke" || mode == "zc" || concurrent) {
      require(device->GetUsedSpace() == 0, "IO 区已有有效数据，拒绝混入旧载荷");
      require(!std::filesystem::exists(out + "/sources.tsv"), "源区清单已存在，拒绝覆盖");
      check(fs->CreateDir("/controlled-zc", IOOptions(), nullptr));
      if (!short_test) for (unsigned i = 0; i < kFillers; ++i) {
        write(*fs, "/controlled-zc/filler" + std::to_string(i) + ".bin", kZoneBytes, i + 10, expected.get(), options);
        if (i % 4 == 3) std::cout << "固定填充进展：" << i + 1 << "/22个完整Zone" << std::endl;
      }
      std::unique_ptr<FSWritableFile> guards[4];
      const unsigned cases = short_test ? 1 : 4;
      for (unsigned i = 0; i < cases; ++i) {
        // 现有相同寿命匹配差为50，优先开新区；LONG区与MEDIUM文件差为1，复用尾部。
        write(*fs, path(i), kValid[i], i, expected.get(), options, Env::WLTH_LONG);
        const auto live = extent(*fs, path(i), kValid[i]);
        require(live.start == live.zone_start, "有效载荷不是 Zone 起点，映射计划不能沿用");
        sources[i] = live.zone_start;
        // 只用于准备同步：重新打开但不追加，写锁让维护快照暂时跳过有效文件。
        // Zone 已由上次 Close 归还，因此无效载荷仍可复用同一区尾部。
        check(fs->ReopenWritableFile(path(i), options, &guards[i], nullptr));
        write(*fs, path(i, true), kZoneBytes - kValid[i], i + 4, expected.get(), options);
        const auto dead = extent(*fs, path(i, true), kZoneBytes - kValid[i]);
        require(dead.zone_start == sources[i] && dead.start == sources[i] + kValid[i], "有效和无效载荷没有连续共享源区");
        Zone* zone = device->GetIOZone(sources[i]);
        require(zone->IsFull() && zone->wp_ == sources[i] + kZoneBytes && zone->used_capacity_ == kZoneBytes,
                "源区未由真实写入填满，或有效量不符");
        require(pressure(*device, sources[i]).sum_block_reads == 0, "初始源区读数非零");
        hot_reads(*fs, i, kBaseReads, scratch.get(), expected.get(), options);
      }
      std::ofstream manifest(out + "/sources.tsv"), csv(out + "/initial.csv");
      require(manifest.good() && csv.good(), "无法创建源区或初始状态记录");
      csv << "组号,读取分布,Zone字节地址,有效字节,真实删除字节,累计4KiB读次数,最大读数,读数总和,达阈值块,空闲比例\n";
      for (unsigned i = 0; i < cases; ++i) {
        // 只删除本程序创建的无效文件，不重置仍包含有效数据的区。
        check(fs->DeleteFile(path(i, true), IOOptions(), nullptr));
        Zone* zone = device->GetIOZone(sources[i]);
        const auto p = pressure(*device, sources[i]);
        require(zone->IsFull() && zone->used_capacity_ == kValid[i] && zone->GetResetState().generation == 0,
                "删除后源区状态不符，或在准备同步结束前已重置");
        require(p.sum_block_reads == kBaseReads && p.max_block_reads == (i % 2 ? 1024 : kBaseReads)
                && p.blocks_at_threshold == (i % 2 ? 0 : 1), "四格初始风险分布不符");
        manifest << sources[i] << '\t' << kValid[i] << '\n';
        csv << i << ',' << (i % 2 ? "八块分散" : "单块集中") << ',' << sources[i] << ',' << kValid[i]
            << ',' << kZoneBytes - kValid[i] << ',' << kBaseReads << ',' << p.max_block_reads << ','
            << p.sum_block_reads << ',' << p.blocks_at_threshold << ',' << free_percent(*device) << '\n';
      }
      manifest.close(); csv.close();
      require(short_test || free_percent(*device) == 10, "固定填充未形成预定的10%空闲观察点");
      if (concurrent) Info(logger, "受控同时维护：准备同步结束，即将释放文件锁，RR/ZC始终开启");
      for (unsigned i = 0; i < cases; ++i) { check(guards[i]->Close(IOOptions(), nullptr)); guards[i].reset(); }
      if (short_test) {
        if (concurrent) wait_for_move(*fs, *device, 0, sources[0]);
        verify(*fs, 1, scratch.get(), expected.get(), options);
        std::cout << "短测通过：真实写满256MiB、删除224MiB、保留32MiB完整比对；未主动Finish" << std::endl;
      } else if (concurrent) {
        // 不指定维护类型，也不改变原 RR 先于 GC 的执行顺序；类型从原生日志核对。
        for (unsigned i = 0; i < 3; ++i) wait_for_move(*fs, *device, i, sources[i]);
        require(extent(*fs, path(3), kValid[3]).zone_start == sources[3], "低风险组在固定补读前已迁移");
        require(pressure(*device, sources[3]).sum_block_reads == kBaseReads, "固定补读起点读数不符");
        Info(logger, "受控同时维护：前三组已迁移，第四组仍在源区，即将固定补读");
        // 仅固定补读边界：写锁暂时使本文件不具备快照覆盖资格，维护线程和开关不停止。
        // 这是测试同步条件，不代表无锁异步业务负载，也不用于吞吐或时延比较。
        check(fs->ReopenWritableFile(path(3), options, &guards[3], nullptr));
        hot_reads(*fs, 3, 24576, scratch.get(), expected.get(), options);
        const auto p = pressure(*device, sources[3]);
        require(p.sum_block_reads == 32768 && p.max_block_reads == kThreshold && p.blocks_at_threshold == 8,
                "固定补读未形成八块各4096的风险");
        std::ofstream future(out + "/future.csv");
        require(future.good(), "无法创建固定补读记录");
        future << "新增4KiB读次数,读数总和,最大读数,达阈值块\n24576," << p.sum_block_reads
               << ',' << p.max_block_reads << ',' << p.blocks_at_threshold << '\n';
        future.close();
        Info(logger, "受控同时维护：固定补读24576次完成，第四组八块各4096，即将释放补读锁");
        check(guards[3]->Close(IOOptions(), nullptr)); guards[3].reset();
        wait_for_move(*fs, *device, 3, sources[3]);
        Info(logger, "受控同时维护：四组实际迁移完成，结束维护观察，另进程关闭RR校验");
        std::cout << "同时维护通过：四组迁移和源重置完成；固定补读新增24576次；未更改RR/ZC调度" << std::endl;
      } else {
        for (unsigned i = 0; i < 2; ++i) wait_for_move(*fs, *device, i, sources[i]);
        for (unsigned i = 2; i < 4; ++i)
          require(extent(*fs, path(i), kValid[i]).zone_start == sources[i], "128MiB组被原空间策略迁移，需核对门槛");
        std::cout << "真实ZC通过：两个32MiB组均迁移并重置，两个128MiB组留在原区；初始空闲10%，RR关闭" << std::endl;
      }
    } else {
      std::ifstream manifest(out + "/sources.tsv");
      for (unsigned i = 0; i < 4; ++i) {
        uint64_t size = 0;
        require(bool(manifest >> sources[i] >> size) && size == kValid[i], "源区清单不符");
      }
      if (mode == "rr-first") {
        for (unsigned i = 2; i < 4; ++i) {
          require(extent(*fs, path(i), kValid[i]).zone_start == sources[i], "RR观察前大组已离开原区");
          const auto p = pressure(*device, sources[i]);
          require(p.sum_block_reads == kBaseReads && p.blocks_at_threshold == (i == 2 ? 1 : 0), "RR前风险不符");
        }
        wait_for_move(*fs, *device, 2, sources[2]);
        require(extent(*fs, path(3), kValid[3]).zone_start == sources[3], "低风险组在补充读取前已迁移");
        std::cout << "第一次后续RR通过：128MiB单块集中组重置，八块分散组仍未达阈值" << std::endl;
      } else if (mode == "future") {
        require(extent(*fs, path(3), kValid[3]).zone_start == sources[3], "未来读之前目标已复用");
        require(pressure(*device, sources[3]).sum_block_reads == kBaseReads, "未来读起点计数不符");
        hot_reads(*fs, 3, 24576, scratch.get(), expected.get(), options);
        const auto p = pressure(*device, sources[3]);
        require(p.sum_block_reads == 32768 && p.max_block_reads == kThreshold && p.blocks_at_threshold == 8, "固定未来读未形成预期风险");
        std::cout << "固定未来读通过：新增24576次，累计32768次，八块各4096读；RR关闭" << std::endl;
      } else if (mode == "rr-second") {
        require(extent(*fs, path(3), kValid[3]).zone_start == sources[3], "第二次RR前目标已迁移");
        require(pressure(*device, sources[3]).blocks_at_threshold == 8, "第二次RR前不是八块达阈值");
        wait_for_move(*fs, *device, 3, sources[3]);
        std::cout << "第二次后续RR通过：128MiB八块分散组迁移并重置，源计数归零" << std::endl;
      } else {
        verify(*fs, 4, scratch.get(), expected.get(), options);
        std::cout << "维护后四组全部320MiB字节比对通过；校验阶段RR关闭" << std::endl;
      }
    }
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "受控ZC测试失败：" << error.what() << std::endl;
    return 1;
  }
}
