// 真实设备几何上的内存编码小测；不写NAND，不冒称完整重挂载。
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "rrzc_flush_origin.h"
#include <iostream>
#include <stdexcept>
using namespace ROCKSDB_NAMESPACE;
static void require(bool b,const char* why){if(!b)throw std::runtime_error(why);}
template<class T>static void check(const T& status){if(!status.ok())throw std::runtime_error(status.ToString());}
int main(int argc,char** argv){
  try {
    require(argc==2,"用法：元数据小测 日志路径");
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(argv[1],&logger));
    ZonedBlockDevice device("nvme0n1",ZbdBackendType::kBlockDev,logger);check(device.Open(false,true));
    auto make=[&](const char* name){auto p=std::make_shared<ZoneFile>(&device,1234,nullptr);p->AddLinkName(name);return p;};
    auto ordinary=make("/rrzc-dbbench/probe.sst");ordinary->MaybeCaptureFlushOrigin();
    require(!ordinary->HasFlushOrigin() && ordinary->ColocationTag()==0,"关闭时普通文件被分类");
    auto explicit_h=make("/rrzc-dbbench/000008.sst");
    {rrzc_flush_origin::Scope scope(true,42);explicit_h->MaybeCaptureFlushOrigin();}
    require(!explicit_h->HasFlushOrigin() && explicit_h->ColocationTag()==1,"显式H没有优先");
    auto not_sst=make("/rrzc-dbbench/probe.blob");
    {rrzc_flush_origin::Scope scope(true,42);not_sst->MaybeCaptureFlushOrigin();}
    require(!not_sst->HasFlushOrigin(),"非SST被分类");
    auto source=make("/rrzc-dbbench/probe.sst");
    {rrzc_flush_origin::Scope scope(true,42);source->MaybeCaptureFlushOrigin();}
    require(source->HasFlushOrigin() && source->ColocationTag()==2 && source->ColocationZones().empty(),"无成员来源标签未捕获");
    std::string encoded;source->EncodeSnapshotTo(&encoded);
    auto restored=std::make_shared<ZoneFile>(&device,1234,nullptr);Slice input(encoded);check(restored->DecodeFrom(&input));
    require(restored->HasFlushOrigin() && restored->FlushOriginJob()==42 && restored->ColocationTag()==2 &&
        restored->ColocationZones().empty() && restored->GetFilename()==source->GetFilename(),"普通回退标签编码恢复失败");
    auto merged=make("/unused");check(merged->MergeUpdate(restored,false));
    require(merged->HasFlushOrigin() && merged->FlushOriginJob()==42,"增量更新丢失来源");
    for(int mode=0;mode<3;++mode){
      std::string bad=encoded;
      if(mode==0)bad.pop_back();
      if(mode==1)bad[bad.size()-12]=2;
      if(mode==2)bad+=encoded.substr(encoded.size()-16);
      auto failed=std::make_shared<ZoneFile>(&device,1234,nullptr);Slice wrong(bad);require(!failed->DecodeFrom(&wrong).ok(),"损坏或重复来源元数据未拒绝");
    }
    std::cout<<"Flush来源元数据通过：显式H优先、普通关闭、非SST排除、无成员标签恢复、更新及损坏拒绝\n";
    return 0;
  }catch(const std::exception& e){std::cerr<<"Flush来源元数据失败："<<e.what()<<'\n';return 1;}
}
