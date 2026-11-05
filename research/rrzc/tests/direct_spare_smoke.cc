// 真实设备分配回归：普通空区为零、当前尾部不足、独占空替补仍可接续。
// 只用文件读写/删除与原GC线程准备，不注入RC、不直接调用迁移或Reset。
#define main archived_spare_helpers_main
#include "v1_execution_smoke_test.cc"
#undef main
#include "plugin/zenfs/fs/rrzc_direct_selector.h"

static std::string seed_name(unsigned i) {
  return "/v1-execution-smoke/seed"+std::to_string(i)+".sst";
}
static void spare_run(ZenFS& fs,ZonedBlockDevice& d,std::shared_ptr<Logger> logger) {
  require(d.GetUsedSpace()==0,"设备并非全空");
  check(fs.CreateDir("/v1-execution-smoke",IOOptions(),nullptr));
  FileOptions o;o.use_direct_reads=o.use_direct_writes=true;Buffer data;
  for(unsigned i=0;i<22;++i)
    write(fs,"/v1-execution-smoke/filler"+std::to_string(i)+".bin",ZoneBytes,100+i,Env::WLTH_MEDIUM,data,o);
  std::unique_ptr<FSWritableFile> guards[4];uint64_t sources[4]={};
  for(unsigned i=0;i<4;++i) {
    const auto name=seed_name(i),dead=name+".dead.bin";
    write(fs,name,62*MiB,20+i,Env::WLTH_LONG,data,o);
    sources[i]=extent(fs,name,62*MiB).zone_start;
    check(fs.ReopenWritableFile(name,o,&guards[i],nullptr));
    write(fs,dead,194*MiB,40+i,Env::WLTH_MEDIUM,data,o);
    require(extent(fs,dead,194*MiB).zone_start==sources[i] && d.GetIOZone(sources[i])->IsFull(),"种子未形成独立满区");
    check(fs.DeleteFile(dead,IOOptions(),nullptr));
  }
  for(auto& guard:guards) {check(guard->Close(IOOptions(),nullptr));guard.reset();}
  auto limit=Clock::now()+std::chrono::seconds(55);bool primed=false;unsigned filled=0;
  while(Clock::now()<limit) {
    unsigned completed=0;
    // 迁移持写锁时快照会暂时跳过文件；未知只继续等待，不能误报丢失。
    for(unsigned i=0;i<4;++i) {
      const auto observed=find(fs,seed_name(i));
      if(observed && observed->length==62*MiB && observed->zone_start!=sources[i])++completed;
    }
    // 每轮回收后立即填满一个普通空区，避免原动态无效门槛升高后阻止后续准备。
    // 已迁移文件的新映射才计完成；源区随后可能被填充复用，不再要求一直为空。
    while(filled<completed) {
      write(fs,"/v1-execution-smoke/filler"+std::to_string(22+filled)+".bin",ZoneBytes,122+filled,Env::WLTH_MEDIUM,data,o);
      ++filled;
    }
    primed=completed==4 && filled==4;
    if(primed)break;
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  require(primed,"原GC未完成四次62MiB种子清理");
  auto reserve=d.GetMigrationReserveObservation();
  require(reserve.known && reserve.present && reserve.valid==248*MiB && reserve.remaining==8*MiB,"未形成248MiB有效和8MiB尾部的真实迁移目标");
  write(fs,file(0),32*MiB,1,Env::WLTH_LONG,data,o);
  auto candidate=extent(fs,file(0),32*MiB);
  std::unique_ptr<FSWritableFile> guard;check(fs.ReopenWritableFile(file(0),o,&guard,nullptr));
  write(fs,file(0,true),224*MiB,10,Env::WLTH_MEDIUM,data,o);
  require(extent(fs,file(0,true),224*MiB).zone_start==candidate.zone_start && d.GetIOZone(candidate.zone_start)->IsFull(),"回归候选未形成独立满区");
  check(fs.DeleteFile(file(0,true),IOOptions(),nullptr));
  reserve=d.GetMigrationReserveObservation();auto spare=d.GetMigrationSpareObservation();
  auto again=d.GetMigrationSpareObservation();rrzc_guard::Bounds bounds;bounds.Add(32*MiB,4096,8,256*MiB);
  require(reserve.known && reserve.valid==248*MiB && reserve.remaining==8*MiB &&
      rrzc_direct::SpareFits(candidate.zone_start,bounds,32*MiB,256*MiB,spare,again),"尾部不足或稳定替补前提未成立");
  ZenFSSnapshot snapshot;ZenFSSnapshotOptions so;so.zone_=so.zone_file_=true;fs.GetZenFSSnapshot(snapshot,so);
  uint64_t ordinary_empty=0;
  for(const auto& z:snapshot.zones_) {
    auto* live=d.GetIOZone(z.start);
    if(live && live->Acquire()) {
      if(live->IsEmpty())++ordinary_empty;
      require(live->Release(),"观察普通空区后释放失败");
    }
  }
  require(!ordinary_empty,"仍有普通空区，不能声称复现漏算独占替补");
  Info(logger,"替补回归准备：源Zone=%lu，有效字节=%lu，当前尾部=%lu，当前有效=%lu，普通空区=%lu，替补Zone=%lu，替补容量=%lu",
      candidate.zone_start,32*MiB,reserve.remaining,reserve.valid,ordinary_empty,spare.zone,spare.remaining);
  check(guard->Close(IOOptions(),nullptr));guard.reset();
  limit=Clock::now()+std::chrono::seconds(16);bool moved=false;
  while(Clock::now()<limit) {
    const auto observed=find(fs,file(0));
    if(observed && observed->length==32*MiB && observed->zone_start==spare.zone &&
        d.GetIOZone(candidate.zone_start)->IsEmpty()){moved=true;break;}
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  require(moved,"没有通过原分配器接续至独占替补并Reset源区");
  Info(logger,"替补回归完成：源Zone=%lu，实际目标=%lu，原尾部不足=1，普通空区为零=1，原分配器接续=1，源已Reset=1",
      candidate.zone_start,spare.zone);
  std::cout<<"真实替补回归通过：普通空区零、尾部8MiB、迁移32MiB至独占替补并回收源区\n";
}
static void spare_verify(ZenFS& fs) {
  FileOptions o;o.use_direct_reads=true;Buffer expected,scratch;
  for(unsigned i=0;i<31;++i) {
    const bool filler=i<26,seed=i>=26 && i<30;
    const auto name=filler ? "/v1-execution-smoke/filler"+std::to_string(i)+".bin" : seed ? seed_name(i-26) : file(0);
    const uint64_t bytes=filler ? ZoneBytes : seed ? 62*MiB : 32*MiB;
    uint64_t size=0;check(fs.GetFileSize(name,IOOptions(),&size,nullptr));require(size==bytes,"回归文件长度错误");
    expected.fill(filler ? 100+i : seed ? 20+i-26 : 1);
    std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile(name,o,&reader,nullptr));
    for(uint64_t off=0;off<bytes;) {
      const size_t length=filler ? 4096 : MiB;Slice result;
      check(reader->Read(off,length,IOOptions(),&result,scratch.get(),nullptr));
      require(result.size()==length && !std::memcmp(result.data(),expected.get()+off%MiB,length),"回归文件载荷不符");
      if(!filler)off+=MiB;else if(!off)off=bytes-4096;else break;
    }
  }
  std::cout<<"只读校验通过：完整280MiB有效数据，26个填充文件首尾4KiB\n";
}
int main(int argc,char** argv) {
  try {
    require(argc==4,"用法：程序 run|verify benefit 结果目录");
    const std::string stage=argv[1],out=argv[3];const bool readonly=stage=="verify";
    require((stage=="run" || readonly) && std::string(argv[2])=="benefit" &&
        out=="/home/femu/rrzc-benefit-device-results/benefit" && std::filesystem::is_directory(out),"回归参数不符");
    const auto path=out+"/zenfs."+stage+".log";require(!std::filesystem::exists(path),"拒绝覆盖日志");
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(path,&logger));logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);check(owned->Open(readonly,true));
    auto* device=owned.get();auto fs=std::make_unique<ZenFS>(owned.release(),FileSystem::Default(),logger);check(fs->Mount(readonly));
    if(readonly)spare_verify(*fs);else spare_run(*fs,*device,logger);
    return 0;
  }catch(const std::exception& e){std::cerr<<"真实替补回归失败："<<e.what()<<'\n';return 1;}
}
