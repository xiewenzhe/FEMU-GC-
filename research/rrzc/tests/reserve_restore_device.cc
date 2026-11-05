// 独立原始设备资源回归；载荷不是ZenFS或RocksDB文件，不报告性能收益。
#include "plugin/zenfs/fs/zbd_zenfs.h"
#include <fcntl.h>
#include <linux/blkzoned.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <array>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>
using namespace rocksdb;
static void require(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
template<class T> static void check(const T& s){if(!s.ok())throw std::runtime_error(s.ToString());}
static std::vector<std::pair<uint64_t,uint8_t>> physical(){
  std::vector<char> buffer(sizeof(blk_zone_report)+64*sizeof(blk_zone));
  auto* report=reinterpret_cast<blk_zone_report*>(buffer.data());report->nr_zones=64;
  int fd=open("/dev/nvme0n1",O_RDONLY|O_CLOEXEC);require(fd>=0,"不能报告设备状态");
  require(!ioctl(fd,BLKREPORTZONE,report) && report->nr_zones==64,"设备状态报告失败");close(fd);
  std::vector<std::pair<uint64_t,uint8_t>> result;
  for(unsigned i=0;i<64;++i)result.emplace_back(report->zones[i].wp,report->zones[i].cond);
  return result;
}
int main(int argc,char** argv){
  try {
    require(argc==2,"用法：预留恢复回归 日志文件");
    constexpr uint64_t Z=128ULL<<20;
    int fd=open("/dev/nvme0n1",O_RDWR|O_DIRECT|O_CLOEXEC);require(fd>=0,"不能打开独立模拟盘");
    void* data=nullptr;require(!posix_memalign(&data,4096,4096),"对齐缓冲失败");std::memset(data,37,4096);
    // 平台max_active=6，ZenFS普通IO额度为4；五个真实部分区使重开接管分支自然执行。
    for(unsigned i=5;i<10;++i){
      require(pwrite(fd,data,4096,i*Z)==4096,"真实顺序写失败");
      blk_zone_range range{};range.sector=i*Z/512;range.nr_sectors=Z/512;
      require(!ioctl(fd,BLKCLOSEZONE,&range),"真实Close失败");
    }
    free(data);close(fd);
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(argv[1],&logger));
    ZonedBlockDevice device("nvme0n1",ZbdBackendType::kBlockDev,logger);check(device.Open(false,true));
    const auto before=device.GetMigrationReserveObservation();
    require(before.present && before.zone==5*Z,"没有重现先接管低编号部分区的实际路径");
    std::array<uint64_t,5> wp{};
    for(unsigned i=5;i<10;++i){
      auto* zone=device.GetIOZone(i*Z);require(zone!=nullptr,"缺少受控部分区");
      zone->used_capacity_=4096;zone->rrzc_content_=i==5?rrzc_content::kOther:rrzc_content::kSst;
      wp[i-5]=zone->wp_;
    }
    const auto state=physical();
    check(device.ReconcileMigrationReserveContent());
    const auto after=device.GetMigrationReserveObservation();
    require(after.present && after.zone==6*Z,"没有把预留角色交换到SST部分区");
    require(physical()==state,"角色恢复改变了设备WP或状态");
    for(unsigned i=5;i<10;++i){
      auto* zone=device.GetIOZone(i*Z);
      require(zone->wp_==wp[i-5] && zone->used_capacity_==4096 && !zone->IsFull(),"恢复改变数据量、WP或封闭状态");
    }
    Zone* metadata=nullptr;
    check(device.AllocateIOZone(Env::WLTH_NOT_SET,IOType::kWAL,&metadata,rrzc_content::kOther));
    require(metadata && metadata->start_==5*Z,"元数据部分区未回到普通分配池");
    check(metadata->Close());require(metadata->Release(),"普通区busy释放失败");device.PutOpenIOZoneToken();
    logger->Flush();
    std::cout<<"真实预留恢复回归通过：先误接管Zone5，恢复至SST Zone6，元数据复用Zone5，WP/有效量/设备状态不变\n";
    return 0;
  }catch(const std::exception& e){std::cerr<<"真实预留恢复回归失败："<<e.what()<<'\n';return 1;}
}
