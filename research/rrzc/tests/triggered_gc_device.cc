// 独立可销毁8GiB设备上的边界验证；只用文件操作，自然等待原GC线程。
#define main archived_triggered_helpers_main
#include "v1_execution_smoke_test.cc"
#undef main
#include "rrzc_space_plan.h"

static void boundary(ZenFS& fs, ZonedBlockDevice& d,
                     const std::shared_ptr<Logger>& logger, const std::string& out) {
  constexpr uint64_t zone_bytes = 128*MiB;
  require(!d.GetUsedSpace(), "边界设备不是初始空盘");
  check(fs.CreateDir("/v1-execution-smoke", IOOptions(), nullptr));
  FileOptions options;
  options.use_direct_reads = options.use_direct_writes = true;
  Buffer data, scratch;
  const std::string live = "/v1-execution-smoke/boundary.live.sst";
  const std::string dead = "/v1-execution-smoke/boundary.dead.sst";
  write(fs, live, 4*MiB, 7, Env::WLTH_LONG, data, options);
  const auto source = extent(fs, live, 4*MiB).zone_start;
  std::unique_ptr<FSWritableFile> guard;
  check(fs.ReopenWritableFile(live, options, &guard, nullptr));
  write(fs, dead, zone_bytes-4*MiB, 8, Env::WLTH_MEDIUM, data, options);
  require(extent(fs, dead, zone_bytes-4*MiB).zone_start == source &&
          d.GetIOZone(source)->IsFull(), "边界有效/无效数据未形成同一满区");
  check(fs.DeleteFile(dead, IOOptions(), nullptr));
  check(guard->Close(IOOptions(), nullptr));
  // 以实际空间统计准备20.5%左右空闲；不修改GC触发、读计数或选择器。
  for (unsigned i=0; i<64; ++i) {
    const auto free = d.GetFreeSpace();
    const auto total = free+d.GetUsedSpace()+d.GetReclaimableSpace();
    const auto target = (total*205+999)/1000;
    if (free<=target+MiB || d.GetIOZone(source)->IsEmpty()) break;
    const auto bytes = std::min(zone_bytes, (free-target)/MiB*MiB);
    require(bytes>0, "无法用真实文件写入接近目标空间");
    write(fs, "/v1-execution-smoke/boundary-filler"+std::to_string(i)+".bin",
          bytes, 100+i, Env::WLTH_MEDIUM, data, options);
    std::cout << "边界准备：填充文件=" << i+1 << "，实际空闲=" << d.GetFreeSpace() << std::endl;
  }
  const auto free = d.GetFreeSpace();
  const auto non_free = d.GetUsedSpace()+d.GetReclaimableSpace();
  const auto need = rrzc_space_plan::Demand(free, non_free, 0);
  // 若GC已在最后一次写入期间完成，交由宿主核对同轮零缺口及完整清理日志。
  if (!d.GetIOZone(source)->IsEmpty())
    require(100*free/(free+non_free)==20 && need.known && !need.bytes,
            "真实输入未形成外层进入GC、内层零缺口的边界");
  Info(logger, "触发边界准备：源Zone=%lu，空闲=%lu，非空闲=%lu，原始缺口=%lu，源已重置=%d",
       source, free, non_free, need.bytes, d.GetIOZone(source)->IsEmpty());
  const auto limit = Clock::now()+std::chrono::seconds(30);
  while (!d.GetIOZone(source)->IsEmpty()) {
    require(Clock::now()<limit, "边界等待30秒后源区仍未清理");
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    logger->Flush();
  }
  const auto destination = extent(fs, live, 4*MiB).zone_start;
  require(destination!=source, "清理后文件映射未改变");
  std::unique_ptr<FSRandomAccessFile> reader;
  check(fs.NewRandomAccessFile(live, options, &reader, nullptr));
  data.fill(7);
  for (uint64_t off=0; off<4*MiB; off+=MiB) {
    Slice value;
    check(reader->Read(off, MiB, IOOptions(), &value, scratch.get(), nullptr));
    require(value.size()==MiB && !std::memcmp(value.data(), data.get(), MiB),
            "边界清理后完整内容错误");
  }
  Info(logger, "触发边界验证完成：源Zone=%lu，目标Zone=%lu，完整校验字节=%lu",
       source, destination, 4*MiB);
  logger->Flush();
  std::cout << "已触发GC边界验证通过：源Reset、映射改变、4MiB完整内容通过" << std::endl;
  std::ofstream result(out+"/边界文件.json");
  result << "{\"源Zone\":" << source << ",\"目标Zone\":" << destination << ",\"校验字节\":" << 4*MiB << "}\n";
}

int main(int argc, char** argv) {
  try {
    require(argc==2, "用法：边界验证程序 结果目录");
    std::shared_ptr<Logger> logger;
    check(Env::Default()->NewLogger(std::string(argv[1])+"/zenfs.run.log", &logger));
    logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned = std::make_unique<ZonedBlockDevice>("nvme0n1", ZbdBackendType::kBlockDev, logger);
    check(owned->Open(false, true));
    auto* device = owned.get();
    ZenFS fs(owned.release(), FileSystem::Default(), logger);
    check(fs.Mount(false));
    boundary(fs, *device, logger, argv[1]);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "已触发GC边界验证失败：" << error.what() << std::endl;
    return 1;
  }
}
