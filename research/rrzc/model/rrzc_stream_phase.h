// 阶段观测仅在原批次维护确认后执行，不发数据读写或维护请求。
#pragma once
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "plugin/zenfs/fs/snapshot.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace rrzc_stream_phase {
inline void Capture(ROCKSDB_NAMESPACE::FileSystem* filesystem,const char* prefix,
                    uint64_t stage,uint64_t windows,uint64_t operations) {
  if(!prefix || !*prefix)throw std::runtime_error("持续阶段缺少请求路径");
  auto* fs=static_cast<ROCKSDB_NAMESPACE::ZenFS*>(filesystem);
  // 使用实际Logger的Flush，不以stdio刷新冒充EnvLogger私有缓存已落盘。
  const auto begin=fs->ObserveStreamLog(0,0);
  const auto before=fs->GetMigrationWindowSnapshot();
  ROCKSDB_NAMESPACE::ZenFSSnapshot s;ROCKSDB_NAMESPACE::ZenFSSnapshotOptions options;
  options.zbd_=options.zone_=true;fs->GetZenFSSnapshot(s,options);
  const auto after=fs->GetMigrationWindowSnapshot();
  const auto observed=fs->ObserveStreamLog(0,0);
  if(begin!=observed || !before.reliable || !after.reliable || !before.enabled || !after.enabled ||
      before.owner!=after.owner || before.zones.size()!=after.zones.size())
    throw std::runtime_error("阶段采样期间存在日志或迁移交错，停止而非冒称精确切点");
  for(const auto& item:before.zones) {
    const auto next=after.Get(item.first);
    if(item.second.active || next.active || item.second.sequence!=next.sequence)
      throw std::runtime_error("阶段采样发生迁移交错");
  }
  const auto cut=fs->ObserveStreamLog(stage,operations);
  const std::string path=std::string(prefix)+".window-"+std::to_string(windows)+".json";
  if(access(path.c_str(),F_OK)==0)throw std::runtime_error("阶段文件已存在，拒绝覆盖");
  std::ofstream out(path);
  out<<"{\"窗口\":"<<windows<<",\"检查点\":"<<stage<<",\"请求\":"<<operations
     <<",\"原日志字节切点\":"<<cut<<",\"空闲字节\":"<<s.zbd_.free_space
     <<",\"有效字节\":"<<s.zbd_.used_space<<",\"满区可回收字节\":"<<s.zbd_.reclaimable_space
     <<",\"Zone\":[";
  bool first=true;
  for(const auto& z:s.zones_) {
    if(!first)out<<',';
    first=false;out<<"{\"编号\":"<<z.start/(128ULL<<20)<<",\"WP\":"<<z.wp
      <<",\"剩余\":"<<z.capacity<<",\"有效\":"<<z.used_capacity<<",\"容量\":"<<z.max_capacity<<'}';
  }
  out<<"]}\n";out.close();
  if(!out)throw std::runtime_error("阶段快照写入失败");
}
}  // 只读阶段观测不设置限速，不恢复或改变在线分类状态。
