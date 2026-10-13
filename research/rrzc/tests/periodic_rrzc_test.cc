// 隔离客机专用的周期轮换负载；不直接调用清理、读刷新或设备重置。
// .sst 是带校验模式的文件系统载荷，不是实际 RocksDB SST。
// 固定周期业务删除会自然回收全无效区，必须与有搬运的 ZC/RR 分开计数。
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include "logging/logging.h"
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "plugin/zenfs/fs/snapshot.h"

using namespace rocksdb;
using Clock = std::chrono::steady_clock;
constexpr uint64_t kMiB = 1ULL << 20;
constexpr unsigned kPeriod = 80;
static void require(bool value, const std::string& message) {
  if (!value) throw std::runtime_error(message);
}
template <typename S> static void check(const S& s) { require(s.ok(), s.ToString()); }
static uint64_t ns(Clock::duration d) {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(d).count();
}
static std::string name(unsigned cycle, const std::string& suffix) {
  return "/rrzc-periodic/c" + std::to_string(cycle) + "." + suffix;
}
static void fill(char* buffer, unsigned seed) {
  for (uint64_t i = 0; i < kMiB; ++i) buffer[i] = (i * 131 + 17 + seed * 31) % 251;
}
struct Buffer {
  std::unique_ptr<char, decltype(&std::free)> data{nullptr, &std::free};
  Buffer() {
    void* raw = nullptr;
    require(posix_memalign(&raw, 4096, kMiB) == 0, "对齐缓冲申请失败");
    data.reset(static_cast<char*>(raw));
  }
  char* get() { return data.get(); }
};
class Workload {
 public:
  ZenFS& fs;
  ZonedBlockDevice& device;
  std::shared_ptr<Logger> logger;
  FileOptions options;
  std::ofstream operations, plan, states, pressure;
  Clock::time_point origin, cycle_start;
  int cycle = -1;
  std::string phase = "初始填充";
  Buffer buffer, scratch, cold_pattern, hot_pattern;
  Workload(ZenFS& f, ZonedBlockDevice& d, std::shared_ptr<Logger> l, const std::string& out)
      : fs(f), device(d), logger(l), operations(out + "/operations.csv"),
        plan(out + "/actual-trace.csv"), states(out + "/states.csv"),
        pressure(out + "/pressure.csv"), origin(Clock::now()), cycle_start(origin) {
    options.use_direct_reads = options.use_direct_writes = true;
    require(operations.good() && plan.good() && states.good() && pressure.good(), "记录文件创建失败");
    operations << "周期,阶段,操作,文件,偏移,字节,开始纳秒,延迟纳秒,排程迟到纳秒,成功\n";
    plan << "周期,阶段,操作,文件,偏移,字节\n";
    states << "周期,阶段,经过纳秒,空闲字节,有效字节,满区可回收字节,空区,部分区,满区,累计IO区成功重置\n";
    pressure << "周期,阶段,经过纳秒,Zone,最大块读数,总读数,达阈值块,主机代际\n";
  }
  template <typename Action> void call(const std::string& kind, const std::string& file,
                                       uint64_t offset, uint64_t bytes, Action action,
                                       Clock::time_point scheduled = Clock::time_point{}) {
    const auto begin = Clock::now();
    const auto status = action();
    const auto end = Clock::now();
    uint64_t late = scheduled == Clock::time_point{} || begin <= scheduled ? 0 : ns(begin-scheduled);
    operations << cycle << ',' << phase << ',' << kind << ',' << file << ',' << offset << ','
               << bytes << ',' << ns(begin-origin) << ',' << ns(end-begin) << ',' << late << ',' << status.ok() << '\n';
    plan << cycle << ',' << phase << ',' << kind << ',' << file << ',' << offset << ',' << bytes << '\n';
    check(status);
  }
  void write(const std::string& file, uint64_t bytes, unsigned seed,
             Env::WriteLifeTimeHint hint, bool paced) {
    require(fs.FileExists(file, IOOptions(), nullptr).IsNotFound(), "拒绝覆盖业务文件");
    fill(buffer.get(), seed);
    std::unique_ptr<FSWritableFile> writer;
    call("创建", file, 0, 0, [&] { return fs.NewWritableFile(file, options, &writer, nullptr); });
    writer->SetWriteLifeTimeHint(hint);
    const auto start = Clock::now();
    for (uint64_t offset = 0; offset < bytes; offset += kMiB) {
      // 每文件64MiB/s的定速突发写入；API延迟不包括此处主动等待。
      auto due = start + std::chrono::nanoseconds(offset/kMiB * 15625000ULL);
      if (paced) std::this_thread::sleep_until(due);
      call("写1MiB", file, offset, kMiB,
           [&] { return writer->Append(Slice(buffer.get(), kMiB), IOOptions(), nullptr); },
           paced ? due : Clock::time_point{});
    }
    call("关闭", file, bytes, 0, [&] { return writer->Close(IOOptions(), nullptr); });
  }
  void remove(const std::string& file) {
    call("删除", file, 0, 0, [&] { return fs.DeleteFile(file, IOOptions(), nullptr); });
  }
  void pause(unsigned seconds) {
    std::this_thread::sleep_until(cycle_start + std::chrono::seconds(seconds));
  }
  void sample(const std::string& label) {
    phase = label;
    const auto elapsed = ns(Clock::now()-origin);
    Info(logger, "周期负载标记：周期=%d，阶段=%s，经过纳秒=%lu", cycle, label.c_str(), elapsed);
    ZenFSSnapshot snapshot;
    ZenFSSnapshotOptions opts;
    opts.zbd_ = opts.zone_ = true;
    fs.GetZenFSSnapshot(snapshot, opts);
    uint64_t empty = 0, partial = 0, full = 0, resets = 0;
    auto generations = device.GetZoneResetStates();
    for (const auto& z : snapshot.zones_) {
      if (z.wp == z.start) ++empty;
      else if (z.capacity == 0) ++full;
      else ++partial;
      resets += generations.at(z.start).generation;
    }
    states << cycle << ',' << label << ',' << elapsed << ',' << snapshot.zbd_.free_space << ','
           << snapshot.zbd_.used_space << ',' << snapshot.zbd_.reclaimable_space << ','
           << empty << ',' << partial << ',' << full << ',' << resets << '\n';
    ZoneReadPressureReport report;
    check(device.GetZoneReadPressure(&report));
    require(report.read_threshold == 4096 && report.blocks_per_zone == 64 && report.zones.size() == 32,
            "不是指定的4096/64块/32区配置");
    for (const auto& p : report.zones) if (generations.count(p.start))
      pressure << cycle << ',' << label << ',' << elapsed << ',' << p.start << ',' << p.max_block_reads
               << ',' << p.sum_block_reads << ',' << p.blocks_at_threshold << ',' << generations.at(p.start).generation << '\n';
    operations.flush(); plan.flush(); states.flush(); pressure.flush();
    require(operations.good() && plan.good() && states.good() && pressure.good(), "记录写入失败");
    // 空间、代际与C1h分次采样，不宣称是原子的全设备状态。
  }
  void reads(unsigned count, unsigned rate, unsigned start_second, const std::string& label) {
    phase = label;
    std::unique_ptr<FSRandomAccessFile> readers[2];
    const std::string files[] = {name(cycle, "cold.sst"), name(cycle, "hot.sst")};
    fill(cold_pattern.get(), 17 + 2*cycle); fill(hot_pattern.get(), 18 + 2*cycle);
    for (unsigned j = 0; j < 2; ++j)
      call("打开读", files[j], 0, 0, [&] { return fs.NewRandomAccessFile(files[j], options, &readers[j], nullptr); });
    for (unsigned i = 0; i < count; ++i) {
      auto due = cycle_start + std::chrono::seconds(start_second) +
                 std::chrono::nanoseconds(i * 1000000000ULL / rate);
      std::this_thread::sleep_until(due);
      for (unsigned j = 0; j < 2; ++j) {
        const uint64_t offset = j == 0 ? (i%8)*65536 : 0;
        Slice result;
        call("读4KiB", files[j], offset, 4096, [&] {
          return readers[j]->Read(offset, 4096, IOOptions(), &result, scratch.get(), nullptr);
        }, due);
        const char* expected = j == 0 ? cold_pattern.get() : hot_pattern.get();
        require(result.size() == 4096 && std::memcmp(result.data(), expected+offset, 4096) == 0,
                "业务读取长度或数据不符");
      }
    }
  }
  void run(unsigned cycles) {
    require(device.GetUsedSpace() == 0, "不是新空设备，拒绝混入旧载荷");
    check(fs.CreateDir("/rrzc-periodic", IOOptions(), nullptr));
    for (unsigned i = 0; i < 24; ++i) {
      write("/rrzc-periodic/filler" + std::to_string(i) + ".bin", 256*kMiB, 200+i, Env::WLTH_MEDIUM, false);
      if (i%8 == 7) std::cout << "初始填充 " << i+1 << "/24个Zone" << std::endl;
    }
    origin = Clock::now();
    for (unsigned c = 0; c < cycles; ++c) {
      cycle = c; cycle_start = origin + std::chrono::seconds(c*kPeriod);
      pause(0); sample("周期开始"); phase = "业务换代";
      if (c > 0) for (const auto& suffix : {"balance0.bin", "balance1.bin", "cold.sst", "hot.sst"})
        remove(name(c-1, suffix));
      phase = "新代写入";
      write(name(c, "cold.sst"), 32*kMiB, 17+2*c, Env::WLTH_LONG, true);
      write(name(c, "cold.dead.bin"), 224*kMiB, 70+2*c, Env::WLTH_MEDIUM, true);
      write(name(c, "hot.sst"), 64*kMiB, 18+2*c, Env::WLTH_LONG, true);
      write(name(c, "hot.dead.bin"), 192*kMiB, 71+2*c, Env::WLTH_MEDIUM, true);
      // 不注入victim地址；只核对本负载所需的空间布局是否真实成立。
      ZenFSSnapshot snapshot;
      ZenFSSnapshotOptions opts; opts.zone_ = opts.zone_file_ = true;
      fs.GetZenFSSnapshot(snapshot, opts);
      for (const auto& type : {"cold", "hot"}) {
        uint64_t live_zone = 0, dead_zone = 0, live_count = 0, dead_count = 0;
        for (const auto& e : snapshot.extents_) {
          if (e.filename == name(c, std::string(type)+".sst")) { live_zone = e.zone_start; ++live_count; }
          if (e.filename == name(c, std::string(type)+".dead.bin")) { dead_zone = e.zone_start; ++dead_count; }
        }
        require(live_count == 1 && dead_count == 1 && live_zone == dead_zone && device.GetIOZone(live_zone)->IsFull(),
                "新代冷热文件未形成各自完整Zone，负载条件不成立");
      }
      sample("新代写入后");
      reads(3584, 512, 10, "阈值以下读热身"); sample("读热身后");
      pause(20); phase = "临时数据失效";
      remove(name(c, "cold.dead.bin")); remove(name(c, "hot.dead.bin")); sample("候选发布");
      reads(768, 32, 20, "后续业务读"); pause(44); sample("后续业务读后");
      pause(52); phase = "后续业务写";
      for (unsigned j = 0; j < 2; ++j)
        write(name(c, "balance"+std::to_string(j)+".bin"), 256*kMiB, 90+2*c+j, Env::WLTH_LONG, true);
      sample("后续业务写后"); pause(80); sample("周期结束");
      std::cout << "周期完成 " << c+1 << '/' << cycles << "，不重启、不人工触发维护" << std::endl;
    }
  }
  void verify_file(const std::string& file, uint64_t bytes, unsigned seed, bool complete) {
    uint64_t actual = 0; check(fs.GetFileSize(file, IOOptions(), &actual, nullptr));
    require(actual == bytes, "最终文件长度不符");
    fill(buffer.get(), seed);
    std::unique_ptr<FSRandomAccessFile> reader;
    check(fs.NewRandomAccessFile(file, options, &reader, nullptr));
    for (uint64_t offset = 0; offset < (complete ? bytes : 4096); offset += kMiB) {
      size_t size = complete ? kMiB : 4096;
      Slice result; check(reader->Read(offset, size, IOOptions(), &result, scratch.get(), nullptr));
      require(result.size() == size && std::memcmp(result.data(), buffer.get(), size) == 0, "最终数据字节不符");
    }
  }
};
int main(int argc, char** argv) {
  try {
    require(argc == 4, "用法：程序 run|verify 周期数 隔离新目录");
    const std::string mode = argv[1], out = argv[3];
    unsigned cycles = std::stoul(argv[2]);
    require(cycles > 0 && cycles <= 100 && (mode == "run" || mode == "verify"), "模式或周期数非法");
    require(out.rfind("/tmp/rrzc-periodic-", 0) == 0 && std::filesystem::is_directory(out), "不是隔离结果目录");
    require(getenv("ZENFS_RR_ENABLE") && std::string(getenv("ZENFS_RR_ENABLE")) == (mode == "run" ? "1" : "0"), "RR开关不符");
    require(!getenv("ZENFS_ZC_TEST_ENABLE") && !getenv("ZENFS_ZC_TEST_SOURCE") && !getenv("ZENFS_RRZC_MODE"), "禁止人工选区和旧趋势机制");
    const std::string logfile = out + "/zenfs."+mode+".log";
    require(!std::filesystem::exists(logfile), "拒绝覆盖日志");
    std::shared_ptr<Logger> logger; check(Env::Default()->NewLogger(logfile, &logger)); logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned = std::make_unique<ZonedBlockDevice>("nvme0n1", ZbdBackendType::kBlockDev, logger);
    check(owned->Open(false, true)); auto* device = owned.get();
    auto fs = std::make_unique<ZenFS>(owned.release(), FileSystem::Default(), logger); check(fs->Mount(false));
    // 校验另建子目录，防止覆盖运行期操作、计划、状态记录。
    std::string records = mode == "verify" ? out+"/verify-records" : out;
    if (mode == "verify") require(std::filesystem::create_directory(records), "校验子目录已存在");
    Workload w(*fs, *device, logger, records);
    if (mode == "run") {
      require(getenv("ZENFS_RRZC_SIMPLE_MODE") &&
              (std::string(getenv("ZENFS_RRZC_SIMPLE_MODE")) == "valid" || std::string(getenv("ZENFS_RRZC_SIMPLE_MODE")) == "weighted"), "简单模式未启用");
      w.run(cycles);
    } else {
      unsigned c = cycles-1;
      w.verify_file(name(c, "cold.sst"), 32*kMiB, 17+2*c, true);
      w.verify_file(name(c, "hot.sst"), 64*kMiB, 18+2*c, true);
      for (unsigned j = 0; j < 2; ++j) w.verify_file(name(c, "balance"+std::to_string(j)+".bin"), 256*kMiB, 90+2*c+j, true);
      for (unsigned i = 0; i < 24; ++i) w.verify_file("/rrzc-periodic/filler"+std::to_string(i)+".bin", 256*kMiB, 200+i, false);
      std::cout << "最终608MiB完整字节比对及24个填充文件首4KiB抽检通过；RR关闭，不计入延迟" << std::endl;
    }
    return 0;
  } catch (const std::exception& e) { std::cerr << "实验失败：" << e.what() << std::endl; return 1; }
}
