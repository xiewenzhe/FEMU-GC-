// 只读C2代际；不读取业务页、不挂载ZenFS、不发送Reset。
#include "plugin/zenfs/fs/zbd_zenfs.h"
#include <iostream>
#include <stdexcept>
#include <unistd.h>
using namespace rocksdb;
int main(int argc,char** argv) {
  try {
    if (argc!=2 || geteuid()!=0) throw std::runtime_error("需要隔离客机root及探测日志路径");
    std::shared_ptr<Logger> logger;
    if (!Env::Default()->NewLogger(argv[1],&logger).ok()) throw std::runtime_error("探测日志创建失败");
    ZonedBlockDevice d("nvme0n1",ZbdBackendType::kBlockDev,logger);
    auto status=d.Open(true,false);if (!status.ok()) throw std::runtime_error(status.ToString());
    rrzc_v1::RecentReport r;status=d.GetZoneRecentReads(&r);
    if (!status.ok() || r.zones.size()!=32 || r.blocks_per_zone!=64 || r.threshold!=4096)
      throw std::runtime_error("设备代际查询或几何不符");
    std::cout<<"{\"会话高\":"<<r.session_hi<<",\"会话低\":"<<r.session_lo
      <<",\"块数每区\":"<<r.blocks_per_zone<<",\"代际\":[";
    bool comma=false;
    for (const auto& b:r.zones) {
      if(comma)std::cout<<',';
      comma=true;std::cout<<b.generation;
    }
    std::cout<<"]}\n";return 0;
  }catch(const std::exception& e){std::cerr<<"代际探测失败："<<e.what()<<'\n';return 1;}
}
