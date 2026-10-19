// 仅全空隔离客机的只读接口核验；不挂载ZenFS，不读取业务页、不写入或Reset。
#include "plugin/zenfs/fs/zbd_zenfs.h"
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <unistd.h>
using namespace rocksdb;
static void Require(bool value,const char* why) { if (!value) throw std::runtime_error(why); }
int main(int argc,char** argv) {
  try {
    Require(argc==2 && geteuid()==0,"仅隔离客机root和独立日志路径允许执行");
    std::shared_ptr<Logger> logger;
    Require(Env::Default()->NewLogger(argv[1],&logger).ok(),"创建独立探测日志失败");
    ZonedBlockDevice device("nvme0n1",ZbdBackendType::kBlockDev,logger);
    const auto open_status=device.Open(true,false);
    if (!open_status.ok()) throw std::runtime_error("只读打开设备失败："+open_status.ToString());
    Require(device.GetNrZones()==32 && device.GetZoneSize()==(256ULL<<20),"不是本轮32区设备");
    rrzc_v1::RecentReport report;
    Require(device.GetZoneRecentReads(&report).ok() && report.threshold==4096,"C2只读查询失败");
    // 不需要额外Zone快照；C2各区RC应为零，原运行器随后再次核对全部Zone为空。
    for (const auto& b:report.zones) Require(b.rc==0,"设备已有读计数，拒绝作为全新样本");
    const uint64_t now=std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
    const auto points=device.GetCostValidityReadings(report,now);
    Require(points.size()==29,"成本有效量接口未覆盖29个IO区");
    for (const auto& p:points) Require(p.known && !p.valid,"全新有效量接口结果不符");
    const auto reserve=device.GetMigrationReserveObservation();
    Require(!reserve.enabled && !reserve.known,"只读核验不应启用或补充迁移预留");
    std::cout<<"对齐字节,IO区数,读取数据页,写入或重置,预留启用,预留可知\n"
             <<device.GetBlockSize()<<",29,0,0,0,0\n";
    return 0;
  } catch(const std::exception& e) { std::cerr<<"成本只读接口核验失败："<<e.what()<<'\n'; return 1; }
}
