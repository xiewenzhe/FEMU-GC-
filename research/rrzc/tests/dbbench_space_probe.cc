// 阶段间只读挂载以取得ZenFS实际空间；不发用户数据读取，不运行GC或RR线程。
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "plugin/zenfs/fs/snapshot.h"
#include <iostream>
#include <stdexcept>
using namespace ROCKSDB_NAMESPACE;
template<class T> static void check(const T& s){if(!s.ok())throw std::runtime_error(s.ToString());}
int main(int argc,char** argv) {
  try {
    if(argc!=2)throw std::runtime_error("用法：空间探针 日志路径");
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(argv[1],&logger));
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);
    check(owned->Open(true,false));ZenFS fs(owned.release(),FileSystem::Default(),logger);check(fs.Mount(true));
    ZenFSSnapshot snapshot;ZenFSSnapshotOptions options;options.zbd_=options.zone_=options.zone_file_=true;
    fs.GetZenFSSnapshot(snapshot,options);
    std::cout<<"{\"空闲字节\":"<<snapshot.zbd_.free_space<<",\"有效字节\":"<<snapshot.zbd_.used_space
      <<",\"满区可回收字节\":"<<snapshot.zbd_.reclaimable_space<<",\"Zone\":[";
    bool first=true;
    for(const auto& z:snapshot.zones_) {
      if(!first)std::cout<<',';
      first=false;std::cout<<"{\"编号\":"<<z.start/(128ULL<<20)<<",\"起点\":"<<z.start<<",\"WP\":"<<z.wp
        <<",\"剩余\":"<<z.capacity<<",\"有效\":"<<z.used_capacity<<",\"容量\":"<<z.max_capacity<<'}';
    }
    std::cout<<"]}\n";return 0;
  }catch(const std::exception& e){std::cerr<<"只读空间探针失败："<<e.what()<<'\n';return 1;}
}
