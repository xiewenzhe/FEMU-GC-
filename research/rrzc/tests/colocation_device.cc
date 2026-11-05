// 受控ZenFS功能载荷；这些.sst不是RocksDB表，不报告性能或维护收益。
#include "plugin/zenfs/fs/fs_zenfs.h"
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
using namespace ROCKSDB_NAMESPACE;
constexpr uint64_t MiB=1ULL<<20;
static void require(bool good,const char* why){if(!good)throw std::runtime_error(why);}
template<class T>static void check(const T& s){if(!s.ok())throw std::runtime_error(s.ToString());}
struct Buffer {
  char* p=nullptr;
  Buffer(){require(!posix_memalign(reinterpret_cast<void**>(&p),4096,MiB),"对齐内存失败");}
  ~Buffer(){free(p);}
};
struct System {
  ZonedBlockDevice* device=nullptr;
  std::unique_ptr<ZenFS> fs;
  explicit System(const std::shared_ptr<Logger>& logger) {
    auto disk=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);
    check(disk->Open(false,true));device=disk.get();
    fs=std::make_unique<ZenFS>(disk.release(),FileSystem::Default(),logger);check(fs->Mount(false));
  }
};
static FileOptions options(){FileOptions o;o.use_direct_reads=o.use_direct_writes=true;return o;}
static void write(System& s,const std::string& path,uint64_t bytes,char value,Env::WriteLifeTimeHint hint) {
  Buffer b;std::memset(b.p,value,MiB);std::unique_ptr<FSWritableFile> f;
  check(s.fs->NewWritableFile(path,options(),&f,nullptr));
  f->SetWriteLifeTimeHint(hint);
  for(uint64_t n=0;n<bytes;n+=MiB)
    check(f->Append(Slice(b.p,std::min<uint64_t>(MiB,bytes-n)),IOOptions(),nullptr));
  check(f->Fsync(IOOptions(),nullptr));check(f->Close(IOOptions(),nullptr));
}
static ZoneExtentSnapshot extent(System& s,const std::string& path) {
  ZenFSSnapshot snapshot;ZenFSSnapshotOptions opt;opt.zone_=opt.zone_file_=true;
  s.fs->GetZenFSSnapshot(snapshot,opt);const ZoneExtentSnapshot* found=nullptr;
  for(const auto& e:snapshot.extents_)if(e.filename==path){require(!found,"功能文件跨多个片段");found=&e;}
  require(found,"功能文件缺少映射");return *found;
}
static void verify(System& s,const std::string& path,uint64_t bytes,char value) {
  Buffer b;std::unique_ptr<FSRandomAccessFile> f;check(s.fs->NewRandomAccessFile(path,options(),&f,nullptr));
  for(uint64_t n=0;n<bytes;n+=MiB) {
    Slice result;const auto size=std::min<uint64_t>(MiB,bytes-n);
    check(f->Read(n,size,IOOptions(),&result,b.p,nullptr));require(result.size()==size,"读回长度不符");
    for(size_t i=0;i<result.size();++i)require(result[i]==value,"读回内容不符");
  }
}
static void sample(System& s,std::ofstream& out,const std::string& stage,const std::string& name) {
  const auto e=extent(s,name);auto* z=s.device->GetIOZone(e.zone_start);
  out<<stage<<','<<name<<','<<e.zone_start<<','<<e.length<<','<<z->wp_<<','<<z->used_capacity_.load()<<'\n';
}
int main(int argc,char** argv) {
  try {
    require(argc==3,"用法：功能程序 结果目录 0|1");const std::string out=argv[1];
    const bool pool=std::string(argv[2])=="1";
#ifndef RRZC_COLOCATION_ADAPTER
    require(!pool,"原程序仅用于K=0对照");
#endif
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(out+"/zenfs.log",&logger));
    logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto s=std::make_unique<System>(logger);check(s->fs->CreateDir("/coloc",IOOptions(),nullptr));
    std::ofstream trace(out+"/布局.csv");trace<<"阶段,文件,Zone,有效字节,WP,Zone有效字节\n";
    write(*s,"/coloc/H.sst",16*MiB,'H',Env::WLTH_LONG);
    write(*s,"/coloc/W.sst",16*MiB,'W',Env::WLTH_SHORT);
    write(*s,"/coloc/N.sst",MiB,'N',Env::WLTH_SHORT);
    for(const auto* n:{"H","W","N"})sample(*s,trace,"初次写入",std::string("/coloc/")+n+".sst");
    const auto h=extent(*s,"/coloc/H.sst"),w=extent(*s,"/coloc/W.sst");
    const auto old_wp=s->device->GetIOZone(h.zone_start)->wp_;
    if(pool) {
      require(h.zone_start==w.zone_start,"H/W没有实际同Zone");
      require(extent(*s,"/coloc/N.sst").zone_start!=h.zone_start,"普通文件进入协同池");
#ifdef RRZC_COLOCATION_ADAPTER
      require(s->device->ColocationPool()->Snapshot().size()==1,"K=1初始成员不唯一");
#endif
    }
    for(const auto* n:{"H","W","N"})verify(*s,std::string("/coloc/")+n+".sst",std::string(n)=="N"?MiB:16*MiB,*n);
    // 关闭并重新挂载同一仍在运行的模拟盘；不将qcow2当成NAND恢复。
    s.reset();s=std::make_unique<System>(logger);
    require(extent(*s,"/coloc/H.sst").zone_start==h.zone_start &&
        s->device->GetIOZone(h.zone_start)->wp_==old_wp,"重挂载改变了H映射或WP");
    write(*s,"/coloc/W2.sst",MiB,'X',Env::WLTH_SHORT);
    sample(*s,trace,"重挂载追加","/coloc/W2.sst");
    if(pool)require(extent(*s,"/coloc/W2.sst").zone_start==h.zone_start,"重挂载未复用Closed协同目标");
    for(const auto* n:{"H","W","N"})verify(*s,std::string("/coloc/")+n+".sst",std::string(n)=="N"?MiB:16*MiB,*n);
    verify(*s,"/coloc/W2.sst",MiB,'X');
    // K=0只验证与原生分配相同的写入/恢复路径，不把差异布局当作重复性失败。
    if(pool) {
      Buffer b;std::unique_ptr<FSRandomAccessFile> reader;
      check(s->fs->NewRandomAccessFile("/coloc/H.sst",options(),&reader,nullptr));
      uint64_t reads=0;
      while(extent(*s,"/coloc/H.sst").zone_start==h.zone_start && reads<2048) {
        Slice result;check(reader->Read(0,4096,IOOptions(),&result,b.p,nullptr));
        require(result.size()==4096 && result[0]=='H',"RR触发期间读回失败");++reads;
      }
      reader.reset();require(reads<2048,"真实读取未触发RR迁移");
      const auto moved=extent(*s,"/coloc/H.sst");
      require(moved.zone_start!=h.zone_start && s->device->GetIOZone(h.zone_start)->IsEmpty(),"RR源未成功Reset");
#ifdef RRZC_COLOCATION_ADAPTER
      require(!s->device->ColocationPool()->Contains(h.zone_start),"Reset后池成员残留");
      require(s->device->ColocationPool()->Contains(moved.zone_start),"必要迁移未接入协同池");
#endif
      for(const auto* n:{"H","W","W2"})sample(*s,trace,"RR完成",std::string("/coloc/")+n+".sst");
      for(const auto* n:{"H","W","W2"})check(s->fs->DeleteFile(std::string("/coloc/")+n+".sst",IOOptions(),nullptr));
      check(s->device->ResetUnusedIOZones());
#ifdef RRZC_COLOCATION_ADAPTER
      require(!s->device->ColocationPool()->Contains(moved.zone_start),"删除后Reset未清理池角色");
#endif
      write(*s,"/coloc/H2.sst",MiB,'Y',Env::WLTH_LONG);verify(*s,"/coloc/H2.sst",MiB,'Y');
      sample(*s,trace,"Reset后重新申请","/coloc/H2.sst");
      std::cout<<"协同池真实功能通过：同区混放、普通隔离、Closed恢复、必要RR迁移、Reset清理；触发前台读="<<reads<<'\n';
    }else std::cout<<"K=0原行为对照及内容校验通过\n";
    logger->Flush();
    return 0;
  }catch(const std::exception& error){std::cerr<<"协同池功能失败："<<error.what()<<'\n';return 1;}
}
