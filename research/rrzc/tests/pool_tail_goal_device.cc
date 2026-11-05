// 业务后共同下限取已归档关闭组空闲1530117632字节；不改原GC资格，不指定victim。
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "plugin/zenfs/fs/snapshot.h"
#include "rrzc_front_order.h"
#include <chrono>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <sys/ioctl.h>
#include <linux/fs.h>
#include <fcntl.h>

using namespace ROCKSDB_NAMESPACE;
template<class T> static void check(const T& s) {
  if(!s.ok())throw std::runtime_error(s.ToString());
}
int main(int argc,char** argv) {
  try {
    if(argc!=3 || std::string(argv[2])!="1530117632")
      throw std::runtime_error("用法：共同空间核验 日志路径 1530117632");
    const char* gate=getenv("ZENFS_RRZC_CAL_GC_GATE");
    const char* greedy=getenv("RRZC_CAL_GREEDY_EXECUTE");
    if(!gate || !*gate || !greedy || std::string(greedy)!="1")
      throw std::runtime_error("只允许显式受控检查及两臂共同Greedy收尾");
    std::ifstream model("/sys/class/nvme/nvme0/model");
    std::string name;std::getline(model,name);
    int fd=open("/dev/nvme0n1",O_RDONLY|O_CLOEXEC);uint64_t bytes=0;
    const bool supported=fd>=0 && ioctl(fd,BLKGETSIZE64,&bytes)==0 && bytes==(8ULL<<30) &&
        name.find("FEMU")!=std::string::npos;
    if(fd>=0)close(fd);
    if(!supported)throw std::runtime_error("只允许独立8GiB FEMU设备");
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(argv[1],&logger));
    // 与ZenFS工厂入口一致；否则默认日志级别会过滤维护采证信息。
    logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);
    check(owned->Open(false,true));
    ZenFS fs(owned.release(),FileSystem::Default(),logger);check(fs.Mount(false));
    const uint64_t goal=1530117632ULL;
    uint64_t first_used=0;
    auto snapshot=[&](uint64_t sequence) {
      ZenFSSnapshot s;ZenFSSnapshotOptions options;
      options.zbd_=options.zone_=options.zone_file_=true;
      fs.GetZenFSSnapshot(s,options);
      if(!sequence)first_used=s.zbd_.used_space;
      if(first_used!=s.zbd_.used_space)
        throw std::runtime_error("无前台写入时有效量改变，停止并保留原始证据");
      std::cout<<"{\"序号\":"<<sequence<<",\"目标字节\":"<<goal
        <<",\"空闲字节\":"<<s.zbd_.free_space<<",\"有效字节\":"<<s.zbd_.used_space
        <<",\"满区可回收字节\":"<<s.zbd_.reclaimable_space
        <<",\"达标\":"<<(s.zbd_.free_space>=goal ? "true":"false")<<"}"<<std::endl;
      logger->Flush();return s.zbd_.free_space;
    };
    uint64_t free=snapshot(0),stalled=0;
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(120);
    for(uint64_t sequence=1;free<goal;++sequence) {
      if(sequence>32 || std::chrono::steady_clock::now()>=deadline)
        throw std::runtime_error("共同空间目标达到预定检查或时间上限");
      // 只请求一次原20%资格检查；不直接调用迁移或放宽候选。
      rrzc_front_order::GcTick(gate,sequence);
      const uint64_t next=snapshot(sequence);
      stalled=next<=free ? stalled+1 : 0;free=next;
      if(stalled>=2) {
        std::cout<<"空间结算终止：未达目标，原因=连续两轮原规则无进展，不放宽候选或触发条件"<<std::endl;
        return 0;
      }
    }
    return 0;
  }catch(const std::exception& error) {
    std::cerr<<"共同空间核验失败："<<error.what()<<std::endl;return 1;
  }
}
