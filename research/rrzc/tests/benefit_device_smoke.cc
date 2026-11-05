// 真实文件I/O功能联调：载荷并非RocksDB SST，不用于性能或节省结论。
#define main archived_smoke_main
#include "v1_execution_smoke_test.cc"
#undef main

static void smoke_run(ZenFS& fs,ZonedBlockDevice& d,std::shared_ptr<Logger> logger,
                      const std::string& out,bool benefit) {
  require(d.GetUsedSpace()==0,"设备并非全空");
  check(fs.CreateDir("/v1-execution-smoke",IOOptions(),nullptr));
  FileOptions options;options.use_direct_reads=options.use_direct_writes=true;
  Buffer data,scratch;
  for(unsigned i=0;i<Fillers;++i)
    write(fs,"/v1-execution-smoke/filler"+std::to_string(i)+".bin",ZoneBytes,100+i,Env::WLTH_MEDIUM,data,options);
  uint64_t sources[2]={};std::unique_ptr<FSWritableFile> guards[2];
  for(unsigned i=0;i<2;++i) {
    const uint64_t bytes=(i?40:16)*MiB;
    write(fs,file(i),bytes,i+1,Env::WLTH_LONG,data,options);
    auto e=extent(fs,file(i),bytes);sources[i]=e.zone_start;
    require(e.start==e.zone_start,"候选不在独立源区起点");
    check(fs.ReopenWritableFile(file(i),options,&guards[i],nullptr));
    write(fs,file(i,true),ZoneBytes-bytes,i+10,Env::WLTH_MEDIUM,data,options);
    auto dead=extent(fs,file(i,true),ZoneBytes-bytes);
    require(dead.zone_start==e.zone_start&&dead.start==e.start+bytes&&d.GetIOZone(e.zone_start)->IsFull(),
        "有效/无效数据未同区");
    check(fs.DeleteFile(file(i,true),IOOptions(),nullptr));
  }
  require(sources[0]!=sources[1],"候选未分区");
  // 保持准备句柄直至真实满区历史够长，防止未完成准备提前参与GC。
  std::this_thread::sleep_for(std::chrono::seconds(33));
  logger->Flush();auto previous=gc_checks(out+"/zenfs.run.log");
  auto limit=Clock::now()+std::chrono::seconds(15);
  while(gc_checks(out+"/zenfs.run.log")==previous) {
    require(Clock::now()<limit,"未观察到原GC节拍");
    std::this_thread::sleep_for(std::chrono::milliseconds(100));logger->Flush();
  }
  std::this_thread::sleep_for(std::chrono::seconds(5));
  const auto before=recent(d);
  require(block(before,sources[0]).rc==0&&block(before,sources[1]).rc==0,"存在非计划读数");
  std::unique_ptr<FSRandomAccessFile> reader;
  check(fs.NewRandomAccessFile(file(1),options,&reader,nullptr));data.fill(2);
  auto start=Clock::now();
  for(uint64_t i=0;i<3904;++i) {
    std::this_thread::sleep_until(start+std::chrono::nanoseconds(i*1000000000ULL/4096));
    Slice result;check(reader->Read(0,4096,IOOptions(),&result,scratch.get(),nullptr));
    require(result.size()==4096&&!std::memcmp(result.data(),data.get(),4096),"预读字节错误");
  }
  require(Clock::now()-start<std::chrono::seconds(2),"固定预读计划超时");
  const auto ready=recent(d);
  require(block(ready,sources[1]).rc==3904&&block(ready,sources[1]).generation==block(before,sources[1]).generation,
      "预读计数或代际不符");
  Info(logger,"收益预算功能释放：GreedyZone=%lu，热Zone=%lu，冷有效字节=%lu，热有效字节=%lu，额外字节=%lu，预读RC=3904，后续固定32读每秒，观察至首次维护上限15秒",
      sources[0],sources[1],16*MiB,40*MiB,24*MiB);
  for(auto& guard:guards){check(guard->Close(IOOptions(),nullptr));guard.reset();}
  std::ofstream reads(out+"/reads.csv");reads<<"序号,计划纳秒,API耗时纳秒\n";
  start=Clock::now();bool moved=false;uint64_t sequence=0;
  for(;sequence<480;++sequence) {
    std::this_thread::sleep_until(start+std::chrono::nanoseconds(sequence*1000000000ULL/32));
    Slice result;auto begin=Clock::now();
    check(reader->Read(0,4096,IOOptions(),&result,scratch.get(),nullptr));
    reads<<sequence<<','<<sequence*1000000000ULL/32<<','
        <<std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-begin).count()<<'\n';
    require(result.size()==4096&&!std::memcmp(result.data(),data.get(),4096),"持续读数据错误");
    const unsigned selected=benefit?1:0,other=benefit?0:1;
    auto chosen=find(fs,file(selected));auto skipped=find(fs,file(other));
    if(chosen&&skipped&&chosen->zone_start!=sources[selected]&&d.GetIOZone(sources[selected])->IsEmpty()) {
      require(skipped->zone_start==sources[other],"首次维护同时改变了另一候选");
      moved=true;break;
    }
  }
  reader.reset();reads.close();require(reads.good(),"读取记录写入失败");
  require(moved,"固定功能窗口没有观察到预期源Reset，不改条件重跑");
  Info(logger,"收益预算功能完成：新入口=%d，已完成首次维护观察，额外搬运场景24MiB，不是性能测试",benefit);
  std::cout<<"功能完成："<<(benefit?"收益预算热区改选":"默认Greedy冷区选择")<<"，数据留待独立只读校验"<<std::endl;
}

static void smoke_verify(ZenFS& fs) {
  FileOptions options;options.use_direct_reads=true;Buffer expected,scratch;
  for(unsigned i=0;i<Fillers+2;++i) {
    bool filler=i>=2;unsigned index=filler?i-2:0;
    auto name=filler?"/v1-execution-smoke/filler"+std::to_string(index)+".bin":file(i);
    uint64_t bytes=filler?ZoneBytes:(i?40:16)*MiB,size=0;
    check(fs.GetFileSize(name,IOOptions(),&size,nullptr));require(size==bytes,"验证长度错误");
    expected.fill(filler?100+index:i+1);
    std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile(name,options,&reader,nullptr));
    for(uint64_t off=0;off<bytes;) {
      size_t length=filler?4096:MiB;Slice result;
      check(reader->Read(off,length,IOOptions(),&result,scratch.get(),nullptr));
      require(result.size()==length&&!std::memcmp(result.data(),expected.get()+off%MiB,length),"只读验证数据错误");
      if(!filler)off+=MiB;else if(off==0)off=bytes-4096;else break;
    }
  }
  for(unsigned i=0;i<2;++i)require(fs.FileExists(file(i,true),IOOptions(),nullptr).IsNotFound(),"退役文件仍存在");
  std::cout<<"只读校验通过：完整56MiB有效数据，24个填充文件首尾4KiB"<<std::endl;
}

int main(int argc,char** argv) {
  try {
    require(argc==4,"用法：程序 run|verify default|benefit 结果目录");
    std::string stage=argv[1],mode=argv[2],out=argv[3];bool readonly=stage=="verify";
    require((stage=="run"||readonly)&&(mode=="default"||mode=="benefit"),"参数非法");
    require(out=="/home/femu/rrzc-benefit-device-results/"+mode&&std::filesystem::is_directory(out),"不是本轮新目录");
    auto owned_log=out+"/zenfs."+stage+".log";require(!std::filesystem::exists(owned_log),"拒绝覆盖日志");
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(owned_log,&logger));logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);
    check(owned->Open(readonly,true));auto* device=owned.get();
    auto fs=std::make_unique<ZenFS>(owned.release(),FileSystem::Default(),logger);check(fs->Mount(readonly));
    if(readonly)smoke_verify(*fs);else smoke_run(*fs,*device,logger,out,mode=="benefit");
    return 0;
  }catch(const std::exception& e){std::cerr<<"收益预算功能失败："<<e.what()<<std::endl;return 1;}
}
