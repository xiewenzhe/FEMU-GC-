// 真实设备回归：同寿命元数据/表双向隔离，热表连续两次RR不能落入元数据区。
#define main archived_content_helpers_main
#include "v1_execution_smoke_test.cc"
#undef main

static std::string hot_name(){return "/v1-execution-smoke/hot.sst";}
static std::string meta_name(){return "/v1-execution-smoke/CURRENT";}
static std::string other_name(){return "/v1-execution-smoke/OPTIONS-test";}
static void verify_content(ZenFS& fs,ZonedBlockDevice& d) {
  FileOptions o;o.use_direct_reads=true;Buffer expected,scratch;
  const std::string names[]={meta_name(),hot_name(),other_name()};
  const uint64_t lengths[]={MiB,16*MiB,MiB};
  for(unsigned i=0;i<3;++i) {
    uint64_t size=0;check(fs.GetFileSize(names[i],IOOptions(),&size,nullptr));require(size==lengths[i],"分类回归长度不符");
    expected.fill(10+i);std::unique_ptr<FSRandomAccessFile> reader;
    check(fs.NewRandomAccessFile(names[i],o,&reader,nullptr));
    for(uint64_t off=0;off<lengths[i];off+=MiB) {
      Slice result;check(reader->Read(off,MiB,IOOptions(),&result,scratch.get(),nullptr));
      require(result.size()==MiB && !std::memcmp(result.data(),expected.get(),MiB),"分类回归完整数据不符");
    }
    const auto e=extent(fs,names[i],lengths[i]);
    require(d.GetIOZone(e.zone_start)->rrzc_content_.load()==(i==1 ? 1 : 2),"重新挂载分类恢复失败");
  }
  std::cout<<"只读校验通过：完整18MiB数据，重新挂载内容类别恢复正确"<<std::endl;
}
static void run_content(ZenFS& fs,ZonedBlockDevice& d,std::shared_ptr<Logger> logger) {
  require(d.IsContentGuardEnabled() && !d.GetUsedSpace(),"内容回归前置条件不符");
  check(fs.CreateDir("/v1-execution-smoke",IOOptions(),nullptr));
  FileOptions o;o.use_direct_reads=o.use_direct_writes=true;Buffer data,scratch;
  // 三份文件寿命相同，不能靠寿命差偶然避开旧错误。
  write(fs,meta_name(),MiB,10,Env::WLTH_NOT_SET,data,o);
  write(fs,hot_name(),16*MiB,11,Env::WLTH_NOT_SET,data,o);
  write(fs,other_name(),MiB,12,Env::WLTH_NOT_SET,data,o);
  const auto mz=extent(fs,meta_name(),MiB).zone_start;
  const auto oz=extent(fs,other_name(),MiB).zone_start;
  auto hot=extent(fs,hot_name(),16*MiB);
  require(mz!=hot.zone_start && oz!=hot.zone_start,"普通分配发生跨类别混放");
  require(d.GetIOZone(mz)->rrzc_content_.load()==2 && d.GetIOZone(hot.zone_start)->rrzc_content_.load()==1,"类别标记不符");
  const auto mixed=fs.RenameFile(hot_name(),hot_name()+".tmp",IOOptions(),nullptr);
  require(mixed.IsNotSupported() && fs.FileExists(hot_name(),IOOptions(),nullptr).ok(),"跨类别改名未安全拒绝");
  data.fill(11);
  for(unsigned round=1;round<=2;++round) {
    hot=extent(fs,hot_name(),16*MiB);const auto source=hot.zone_start;
    const auto generation=d.GetIOZone(source)->GetResetState().generation;
    std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile(hot_name(),o,&reader,nullptr));
    const auto start=Clock::now();
    for(unsigned i=0;i<4096;++i) {
      std::this_thread::sleep_until(start+std::chrono::nanoseconds(uint64_t(i)*1000000000ULL/4096));
      Slice result;check(reader->Read(0,4096,IOOptions(),&result,scratch.get(),nullptr));
      require(result.size()==4096 && !std::memcmp(result.data(),data.get(),4096),"真实热读数据不符");
    }
    reader.reset();bool moved=false;const auto limit=Clock::now()+std::chrono::seconds(20);
    while(Clock::now()<limit) {
      auto e=find(fs,hot_name());
      if(e && e->zone_start!=source && d.GetIOZone(source)->GetResetState().generation==generation+1) {
        require(e->zone_start!=mz && e->zone_start!=oz && d.GetIOZone(e->zone_start)->rrzc_content_.load()==1,"RR迁入非SST区");
        Info(logger,"内容回归RR完成：轮次=%u，源Zone=%lu，目标Zone=%lu，元数据Zone=%lu/%lu，源代际=%lu/%lu，真实读取=4096",
            round,source,e->zone_start,mz,oz,generation,generation+1);
        moved=true;break;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    require(moved,"原RR未在20秒内完成，禁止伪称维护成功");
  }
  std::cout<<"真实内容回归通过：同寿命普通写入双向隔离，连续两次RR未迁入非SST区"<<std::endl;
}
int main(int argc,char** argv) {
  try {
    require(argc==4,"用法：程序 run|verify benefit 新结果目录");
    const std::string stage=argv[1],out=argv[3];const bool readonly=stage=="verify";
    require((readonly || stage=="run") && std::string(argv[2])=="benefit" && out=="/home/femu/rrzc-benefit-device-results/benefit","参数不符");
    const auto log=out+"/zenfs."+stage+".log";require(!std::filesystem::exists(log),"拒绝覆盖日志");
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(log,&logger));logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);check(owned->Open(readonly,true));
    auto* d=owned.get();auto fs=std::make_unique<ZenFS>(owned.release(),FileSystem::Default(),logger);check(fs->Mount(readonly));
    if(readonly)verify_content(*fs,*d);else run_content(*fs,*d,logger);
    return 0;
  }catch(const std::exception& e){std::cerr<<"内容回归失败："<<e.what()<<std::endl;return 1;}
}
