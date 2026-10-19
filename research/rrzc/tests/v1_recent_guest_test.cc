// 新隔离客机专用：真实后端查询与原始区定量读取，不挂载或初始化ZenFS。
#include "plugin/zenfs/fs/zbdlib_zenfs.h"
#include <linux/blkzoned.h>
#include <sys/ioctl.h>
#include <fcntl.h>
#include <unistd.h>
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <thread>

using namespace rocksdb;
using namespace rrzc_v1;
static unsigned checks;
static void Require(bool value,const char* why) {
  ++checks; if (!value) throw std::runtime_error(why);
}
static void Check(const IOStatus& status) {
  ++checks; if (!status.ok()) throw std::runtime_error(status.ToString());
}
static RecentReport Query(ZbdlibBackend& backend,const char* stage) {
  RecentReport report; Check(backend.GetZoneRecentReads(&report));
  Require(report.threshold==4096 && report.blocks_per_zone==64 && report.zones.size()==32,
          "实际设备的阈值或几何不匹配");
  for (const auto& b:report.zones)
    std::cout<<"观测,"<<stage<<','<<b.zone_index<<','<<b.zone<<','
      <<report.session_hi<<','<<report.session_lo<<','<<b.generation<<','<<b.block_id<<','
      <<b.ch<<','<<b.lun<<','<<b.plane<<','<<b.block<<','<<b.snapshot_ns<<','
      <<b.epoch_start_ns<<','<<b.rc<<','<<b.delta10<<','<<b.delta30<<','<<b.flags<<'\n';
  std::cout.flush(); return report;
}
static void Pause(unsigned seconds) {
  std::cout<<"等待窗口："<<seconds<<"秒\n"<<std::flush;
  std::this_thread::sleep_for(std::chrono::seconds(seconds));
}
int main(int argc,char** argv) {
  try {
    Require(argc==2,"用法：程序 query|raw-window-test|cache-flush-check");
    const std::string mode=argv[1];
    Require(mode=="query" || mode=="raw-window-test" || mode=="cache-flush-check","不支持的测试模式");
    Require(geteuid()==0,"真实NVMe日志查询需要隔离客机root");
    ZbdlibBackend backend("nvme0n1"); unsigned max_active=0,max_open=0;
    Check(backend.Open(mode=="query",false,&max_active,&max_open));
    Require(backend.GetZoneSize()==(256ULL<<20) && backend.GetNrZones()==32,
            "不是本轮8GiB、32区设备");
    std::cout<<"类型,阶段,区索引,区字节地址,会话高位,会话低位,设备代际,块编号,通道,LUN,plane,block,快照纳秒,代际起点纳秒,当前RC,十秒增量,三十秒增量,窗口标志\n";
    auto report=Query(backend,"初始查询");
    ZoneReadPressureReport pressure; Check(backend.GetZoneReadPressure(&pressure));
    Require(pressure.read_threshold==report.threshold && pressure.zones.size()==32,
            "旧C1h不兼容或阈值不一致");
    if (mode=="query") {
      std::cout<<"真实只读查询通过："<<checks<<"项检查，未写入或Reset设备\n";
      return 0;
    }
    // 本模式仅占用明确的第31区。全部区必须为空；不触碰系统盘，不格式化设备。
    constexpr uint64_t start=31ULL*(256ULL<<20), bytes=4ULL<<20;
    auto zones=backend.ListZones(); Require(bool(zones),"报告Zone失败");
    if (mode=="cache-flush-check") {
      // 仅验证本轮失败样本：前31区全空，第31区恰好写了4MiB且没有已映射读取。
      for (uint32_t i=0; i<31; ++i)
        Require(backend.ZoneWp(zones,i)==backend.ZoneStart(zones,i) &&
                report.zones[i].rc==0,"其他区不是本轮全空状态");
      Require(backend.ZoneWp(zones,31)==start+bytes && report.zones[31].rc==0,
              "不是本轮已保留的4MiB失败样本，拒绝追加写入或Reset");
      int fd=open("/dev/nvme0n1",O_RDWR|O_DIRECT);
      Require(fd>=0,"打开原失败样本设备失败");
      void* allocated=nullptr; Require(posix_memalign(&allocated,4096,4096)==0,"对齐分配失败");
      std::unique_ptr<void,decltype(&free)> buffer(allocated,&free);
      std::memset(buffer.get(),0x5a,4096);
      Require(pwrite(fd,buffer.get(),4096,start+bytes)==4096,"追加4KiB缓存触发写失败");
      Require(pread(fd,buffer.get(),4096,start)==4096,"缓存刷新后的定量读失败");
      const auto* p=static_cast<const unsigned char*>(buffer.get());
      Require(std::all_of(p,p+4096,[](unsigned char b){return b==0x5a;}),"缓存刷新后内容不一致");
      auto flushed=Query(backend,"追加写后一次读").zones[31];
      Require(flushed.rc==1 && flushed.delta10==1 && flushed.delta30==1,
              "追加写没有使原载荷进入映射或计数仍异常");
      blk_zone_range range={.sector=start/512,.nr_sectors=(256ULL<<20)/512};
      Require(ioctl(fd,BLKRESETZONE,&range)==0,"只清理本轮失败样本第31区失败");
      auto cleared=Query(backend,"失败样本清理后").zones[31];
      Require(cleared.generation==flushed.generation+1 && cleared.rc==0 && cleared.flags==kEnabled,
              "失败样本Reset代际或清零异常");
      Require(close(fd)==0,"关闭测试设备失败");
      std::cout<<"缓存延迟刷入原因验证通过："<<checks<<"项检查，仅清理本轮第31区测试载荷\n";
      return 0;
    }
    for (uint32_t i=0; i<32; ++i)
      Require(backend.ZoneWp(zones,i)==backend.ZoneStart(zones,i) &&
          !backend.ZoneIsActive(zones,i) && !backend.ZoneIsOpen(zones,i),
          "存在非空或活跃区，拒绝原始写入");
    const int fd=open("/dev/nvme0n1",O_RDWR|O_DIRECT);
    Require(fd>=0,"打开本轮NVMe设备失败");
    void* allocated=nullptr; Require(posix_memalign(&allocated,4096,bytes)==0,"对齐分配失败");
    std::unique_ptr<void,decltype(&free)> buffer(allocated,&free);
    std::memset(buffer.get(),0x5a,bytes);
    Require(pwrite(fd,buffer.get(),bytes,start)==ssize_t(bytes),"4MiB定量载荷写入失败");
    // 当前生产路径在下一写入到来时刷满缓存；只修正载荷准备，不改设备逻辑。
    Require(pwrite(fd,buffer.get(),4096,start+bytes)==4096,"触发前一4MiB缓存刷入的追加写失败");
    auto read=[&](uint64_t offset,unsigned count) {
      for (unsigned n=0; n<count; ++n) {
        Require(pread(fd,buffer.get(),4096,start+offset)==4096,"定量读取失败");
        const auto* p=static_cast<const unsigned char*>(buffer.get());
        Require(std::all_of(p,p+4096,[](unsigned char b){return b==0x5a;}),"定量读取字节不一致");
      }
    };
    Pause(31);
    read(0,20); report=Query(backend,"块A二十次");
    auto a=report.zones[31];
    Require(a.rc==20 && a.delta10==20 && a.delta30==20 && a.flags==7,
            "块A计数、窗口增量或完整性不符合定量读取");
    // 128KiB是当前每die编程单元；真实身份是否不同必须通过查询确认。
    read(128ULL<<10,35); report=Query(backend,"最大块切换到B");
    auto b=report.zones[31];
    Require(b.block_id!=a.block_id && b.generation==a.generation &&
            b.rc==35 && b.delta10==35 && b.delta30==35,
            "最大块切换未返回块B自身35个事件");
    Pause(11); report=Query(backend,"十秒窗口到期");
    b=report.zones[31]; Require(b.rc==35 && b.delta10==0 && b.delta30==35,
            "十秒窗口到期未保持三十秒事件");
    read(128ULL<<10,7); report=Query(backend,"块B再七次");
    b=report.zones[31]; Require(b.rc==42 && b.delta10==7 && b.delta30==42,
            "块B后续读取计数不匹配");
    Pause(20); report=Query(backend,"早期事件离开三十秒");
    b=report.zones[31]; Require(b.rc==42 && b.delta10==0 && b.delta30==7,
            "滚动三十秒窗口未移除早期事件");
    // 只重置刚写入的第31区，不做全盘Reset或ZenFS初始化。
    blk_zone_range range={.sector=start/512,.nr_sectors=(256ULL<<20)/512};
    Require(ioctl(fd,BLKRESETZONE,&range)==0,"第31区Reset失败");
    report=Query(backend,"Reset后"); auto reset=report.zones[31];
    Require(reset.generation==b.generation+1 && reset.rc==0 &&
            reset.delta10==0 && reset.delta30==0 && reset.flags==kEnabled,
            "Reset代际、清零或窗口预热标志异常");
    Pause(11); report=Query(backend,"Reset十一秒");
    Require(report.zones[31].flags==(kEnabled|kValid10),"Reset十秒窗口标志异常");
    Pause(20); report=Query(backend,"Reset三十一秒");
    Require(report.zones[31].flags==7 && report.zones[31].rc==0,
            "Reset三十秒窗口标志或RC异常");
    for (uint32_t i=0; i<31; ++i)
      Require(report.zones[i].rc==0 && report.zones[i].generation==0,
              "未参与测试的区出现计数或Reset变化");
    Require(close(fd)==0,"关闭测试设备失败");
    std::cout<<"真实逐块窗口/身份切换/Reset短测通过："<<checks<<"项检查\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr<<"未完成："<<error.what()<<"；保留原始记录，不自动重跑或全盘Reset\n";
    return 1;
  }
}
