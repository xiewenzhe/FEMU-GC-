// 只读检查本轮缩小功能盘的真实代际和最大块RC，不发业务读取或Reset。
#include "plugin/zenfs/fs/zbd_zenfs.h"
#include <iostream>
#include <stdexcept>
#include <unistd.h>
using namespace rocksdb;
int main(int argc,char** argv) {
  try {
    if (argc!=2 || geteuid()!=0) throw std::runtime_error("需要客机root与探测日志路径");
    std::shared_ptr<Logger> logger;
    auto s=Env::Default()->NewLogger(argv[1],&logger);
    if (!s.ok()) throw std::runtime_error(s.ToString());
    ZonedBlockDevice d("nvme0n1",ZbdBackendType::kBlockDev,logger);
    s=d.Open(true,false);if (!s.ok()) throw std::runtime_error(s.ToString());
    rrzc_v1::RecentReport r;s=d.GetZoneRecentReads(&r);
    if (!s.ok() || r.zones.size()!=32 || r.blocks_per_zone!=128 || r.threshold!=1024)
      throw std::runtime_error("近期统计或本轮4GiB几何不符");
    std::cout<<"{\"会话高\":"<<r.session_hi<<",\"会话低\":"<<r.session_lo
      <<",\"每区块数\":"<<r.blocks_per_zone<<",\"RR阈值\":"<<r.threshold<<",\"Zone\":[";
    bool comma=false;
    for (const auto& b:r.zones) {
      if(comma)std::cout<<',';
      comma=true;
      std::cout<<"{\"地址\":"<<b.zone<<",\"代际\":"<<b.generation
        <<",\"块\":"<<b.block_id<<",\"RC\":"<<b.rc<<'}';
    }
    std::cout<<"]}\n";
  }catch(const std::exception& e){std::cerr<<"设备核验失败："<<e.what()<<'\n';return 1;}
}
