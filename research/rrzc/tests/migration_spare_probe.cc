// 全空隔离FEMU上的真实资源接口测试；手动维护测试数据的有效量，不冒充文件系统RR实验。
#include "plugin/zenfs/fs/zbd_zenfs.h"
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <set>
#include <stdexcept>
#include <unistd.h>
using namespace rocksdb;
static constexpr uint32_t MiB = 1U << 20;
static void Need(bool ok, const char* why) {
  if (!ok) throw std::runtime_error(why);
}
static void Check(const IOStatus& status) {
  if (!status.ok()) throw std::runtime_error(status.ToString());
}
static void FinishOrdinary(ZonedBlockDevice& device, Zone* zone) {
  Check(zone->Finish());
  Need(zone->Release(), "普通区独占权释放失败");
  device.PutActiveIOZoneToken();
  device.PutOpenIOZoneToken();
}
int main(int argc, char** argv) {
  try {
    Need(argc == 3 && geteuid() == 0, "必须由隔离客机root指定日志路径和off/on");
    const bool enabled = std::string(argv[2]) == "on";
    Need(enabled || std::string(argv[2]) == "off", "未知测试模式");
    Need(getenv("ZENFS_RRZC_MIGRATION_SPARE") &&
         std::string(getenv("ZENFS_RRZC_MIGRATION_SPARE")) == (enabled ? "1" : "0"),
         "测试模式与实际开关不符");
    std::shared_ptr<Logger> logger;
    Need(Env::Default()->NewLogger(argv[1], &logger).ok(), "创建日志失败");
    logger->SetInfoLogLevel(DEBUG_LEVEL);
    ZonedBlockDevice device("nvme0n1", ZbdBackendType::kBlockDev, logger);
    Check(device.Open(false, true));
    Need(device.GetNrZones() == 32 && device.GetZoneSize() == 256ULL * MiB,
         "不是预定8GiB设备");
    for (uint64_t offset = 3ULL * 256 * MiB; offset < 8ULL << 30; offset += 256ULL * MiB) {
      auto* zone = device.GetIOZone(offset);
      Need(zone && zone->IsEmpty(), "设备不是全空，拒绝写入");
    }
    void* raw = nullptr;
    Need(posix_memalign(&raw, 4096, 16 * MiB) == 0, "对齐缓冲分配失败");
    std::unique_ptr<char, decltype(&free)> buffer(static_cast<char*>(raw), free);
    auto write = [&](Zone* zone, uint32_t bytes, unsigned char value) {
      std::memset(buffer.get(), value, bytes);
      const auto offset = zone->wp_;
      Check(zone->Append(buffer.get(), bytes));
      zone->used_capacity_ += bytes;
      std::memset(buffer.get(), 0, bytes);
      Need(device.Read(buffer.get(), offset, bytes, true) == static_cast<int>(bytes), "写后读长度错误");
      Need(std::all_of(buffer.get(), buffer.get() + bytes,
           [value](char byte) { return static_cast<unsigned char>(byte) == value; }), "写后数据校验失败");
    };
    Zone* source = nullptr;
    Check(device.AllocateIOZone(Env::WLTH_LONG, IOType::kUnknown, &source));
    Need(source != nullptr, "无法分配测试源区");
    for (unsigned i = 0; i < 15; ++i) write(source, 16 * MiB, i + 1);
    FinishOrdinary(device, source);
    const auto generation = source->GetResetState().generation;

    Zone* current = nullptr;
    Check(device.TakeMigrateZone(&current, Env::WLTH_LONG, 83 * MiB));
    Need(current != nullptr, "无法取得当前迁移区");
    for (unsigned i = 0; i < 5; ++i) write(current, 16 * MiB, 80);
    write(current, 3 * MiB, 80);
    Check(device.ReleaseMigrateZone(current));
    Need(current->capacity_ == 173ULL * MiB, "预置尾部容量不符");

    Zone* small_source = nullptr;
    unsigned fillers = 0;
    for (; fillers < 29; ++fillers) {
      Zone* zone = nullptr;
      Check(device.AllocateIOZone(Env::WLTH_LONG, IOType::kUnknown, &zone));
      if (!zone) break;
      write(zone, 4096, 99);
      FinishOrdinary(device, zone);
      if (!small_source) small_source = zone;
    }
    Need(fillers == (enabled ? 26U : 27U), "普通可分配空区数不符");
    std::set<uint64_t> targets;
    unsigned copied = 0;
    for (; copied < 15; ++copied) {
      const uint64_t offset = source->start_ + copied * 16ULL * MiB;
      Need(device.Read(buffer.get(), offset, 16 * MiB, true) == 16 * MiB, "读取源数据失败");
      Need(std::all_of(buffer.get(), buffer.get() + 16 * MiB,
           [copied](char byte) { return static_cast<unsigned char>(byte) == copied + 1; }), "源数据内容不符");
      Zone* target = nullptr;
      Check(device.TakeMigrateZone(&target, Env::WLTH_LONG, 16 * MiB));
      if (!target) break;
      const uint64_t dest = target->wp_;
      Check(target->Append(buffer.get(), 16 * MiB));
      target->used_capacity_ += 16 * MiB;
      std::memset(buffer.get(), 0, 16 * MiB);
      Need(device.Read(buffer.get(), dest, 16 * MiB, true) == 16 * MiB, "迁移目标读取失败");
      Need(std::all_of(buffer.get(), buffer.get() + 16 * MiB,
           [copied](char byte) { return static_cast<unsigned char>(byte) == copied + 1; }), "迁移目标内容不符");
      // 仅在目标数据校验后模拟解除源片段引用；任何失败均不Reset源区。
      source->used_capacity_ -= 16 * MiB;
      targets.insert(target->start_);
      Check(device.ReleaseMigrateZone(target));
    }
    if (!enabled) {
      Need(copied == 10 && source->used_capacity_ == 80ULL * MiB &&
           source->GetResetState().generation == generation, "关闭路径没有复现预期接续不足");
      std::cout << "{\"模式\":\"关闭\",\"已复制片段\":10,\"源未Reset\":true,\"预期阻塞复现\":true}\n";
      return 0;
    }
    Need(copied == 15 && targets.size() == 2 && source->used_capacity_ == 0,
         "未跨两个目标完成全部片段");
    Check(device.ResetUnusedIOZones());
    Need(source->IsEmpty() && source->IsBusy() &&
         source->GetResetState().generation == generation + 1, "源区未Reset并补入替补");
    Zone* ordinary = nullptr;
    Check(device.AllocateIOZone(Env::WLTH_LONG, IOType::kUnknown, &ordinary));
    Need(ordinary == nullptr, "普通写入不应抢走唯一空替补");

    // 再执行一次小源区回收，确认已有替补时新回收空间能回到普通写入池。
    Zone* target = nullptr;
    Check(device.TakeMigrateZone(&target, Env::WLTH_LONG, 4096));
    Need(target && small_source, "无法取得小源区回收目标");
    Need(device.Read(buffer.get(), small_source->start_, 4096, true) == 4096,
         "读取小源区失败");
    Need(std::all_of(buffer.get(), buffer.get() + 4096,
         [](char byte) { return static_cast<unsigned char>(byte) == 99; }), "小源区内容错误");
    write(target, 4096, 99);
    small_source->used_capacity_ = 0;
    Check(device.ReleaseMigrateZone(target));
    Check(device.ResetUnusedIOZones());
    Check(device.AllocateIOZone(Env::WLTH_LONG, IOType::kUnknown, &ordinary));
    Need(ordinary && ordinary != source, "回收后普通写入未恢复或抢走替补");
    write(ordinary, 4096, 111);
    FinishOrdinary(device, ordinary);
    std::cout << "{\"模式\":\"开启\",\"已复制片段\":15,\"目标数\":2,\"源Reset并补充替补\":true,\"普通写入恢复\":true,\"全部复制数据校验\":true}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "迁移替补资源测试失败：" << error.what() << '\n';
    return 1;
  }
}
