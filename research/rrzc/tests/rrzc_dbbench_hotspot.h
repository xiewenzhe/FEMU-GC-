// 仅定制db_bench负载：独立读写热点、固定请求数、只读SST和Zone映射采样。
#pragma once
#include <atomic>
#include <cerrno>
#include <cstdlib>
#include <fstream>
#include <stdexcept>
#include <string>
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "plugin/zenfs/fs/snapshot.h"

namespace rrzc_dbbench_hotspot {
inline uint64_t Number(const char* name, uint64_t fallback) {
  const char* text=std::getenv(name);
  if(!text)return fallback;
  if(!*text || *text=='-')throw std::runtime_error("热点参数不是非负整数");
  char* end=nullptr;errno=0;
  auto n=std::strtoull(text,&end,10);
  if(errno || *end)throw std::runtime_error("热点参数格式或范围错误");
  return n;
}
inline bool Enabled() {return Number("RRZC_DB_HOTSPOT",0)==1;}
inline uint64_t Read(uint64_t random,uint64_t total) {
  const auto n=Number("RRZC_DB_READ_KEYS",4096);
  if(!n || n>=total)throw std::runtime_error("热读范围非法");
  return random%n;
}
inline uint64_t Write(uint64_t random,uint64_t total) {
  const auto first=Number("RRZC_DB_READ_KEYS",4096);
  const auto count=Number("RRZC_DB_WRITE_KEYS",61440);
  const auto percent=Number("RRZC_DB_WRITE_HOT_PERCENT",80);
  if(!first || !count || first>=total || count>total-first || percent>100)
    throw std::runtime_error("热写范围或比例非法");
  // 两臂同一随机数决定80%相邻热写、20%其他范围写；不依赖维护结果。
  const bool hot=random%100<percent;
  random/=100;
  return first+random%(hot?count:total-first);
}
inline std::string Hex(const std::string& value) {
  static constexpr char digits[]="0123456789abcdef";
  std::string out;
  for(unsigned char c:value){out+=digits[c>>4];out+=digits[c&15];}
  return out;
}
inline void Layout(ROCKSDB_NAMESPACE::DB* db,ROCKSDB_NAMESPACE::FileSystem* filesystem,bool zns) {
  const char* prefix=std::getenv("RRZC_DB_LAYOUT_PREFIX");
  if(!Enabled() || !prefix)return;
  static std::atomic<uint64_t> next{0};
  const auto id=next.fetch_add(1);
  const std::string path=std::string(prefix)+"-"+std::to_string(id);
  std::vector<ROCKSDB_NAMESPACE::LiveFileMetaData> files;
  db->GetLiveFilesMetaData(&files);
  std::ofstream meta(path+".sst.csv");
  meta<<"文件,层级,字节,最小键十六进制,最大键十六进制\n";
  for(const auto& f:files)meta<<f.name<<','<<f.level<<','<<f.size<<','<<Hex(f.smallestkey)<<','<<Hex(f.largestkey)<<'\n';
  meta.close();if(!meta)throw std::runtime_error("SST映射写出失败");
  if(zns) {
    // 本入口未启用包装文件系统；fs_uri直接构造ZenFS，不使用RTTI。
    auto* fs=static_cast<ROCKSDB_NAMESPACE::ZenFS*>(filesystem);
    ROCKSDB_NAMESPACE::ZenFSSnapshot snapshot;
    ROCKSDB_NAMESPACE::ZenFSSnapshotOptions options;options.zone_=options.zone_file_=true;
    fs->GetZenFSSnapshot(snapshot,options);
    std::ofstream ext(path+".extent.csv");
    ext<<"文件,Zone,起点,字节\n";
    for(const auto& e:snapshot.extents_)ext<<e.filename<<','<<e.zone_start<<','<<e.start<<','<<e.length<<'\n';
    ext.close();if(!ext)throw std::runtime_error("Zone映射写出失败");
  }
}
}  // 定制负载辅助命名空间结束。
