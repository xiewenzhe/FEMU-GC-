// 真实ZenFS受控短对照；复用文件映射工具，载荷不是RocksDB真实SST或性能负载。
#define main archived_all_blocks_helpers_main
#include "v1_execution_smoke_test.cc"
#undef main
#include "rrzc_all_blocks_io.h"
#include <array>
#include <endian.h>
#include <linux/blkzoned.h>
#include <sys/ioctl.h>
#include <fcntl.h>
#include <unistd.h>

static constexpr uint64_t Z=128*MiB;
static constexpr unsigned N=22;
static std::string name(unsigned i,bool dead=false) {
  return "/v1-execution-smoke/block"+std::to_string(i)+(dead?".dead.sst":".live.sst");
}
static rrzc_all_blocks::Report all(ZonedBlockDevice& d,uint64_t zone) {
  rrzc_all_blocks::Report report;std::string error;
  require(rrzc_all_blocks::QueryPath(d.GetFilename(),32,128,zone/Z,Z,&report,&error),error);
  return report;
}
static std::array<uint64_t,9> strict_stats() {
  std::array<uint64_t,9> bytes{};nvme_admin_cmd cmd{};cmd.opcode=2;cmd.nsid=1;
  cmd.addr=reinterpret_cast<uint64_t>(bytes.data());cmd.data_len=72;cmd.cdw10=(17U<<16)|0xc3;
  int fd=open("/dev/nvme0n1",O_RDONLY|O_CLOEXEC);require(fd>=0,"无法打开严格RR统计");
  const int result=ioctl(fd,NVME_IOCTL_ADMIN_CMD,&cmd);close(fd);require(!result,"严格RR统计失败");
  for(auto& x:bytes)x=le64toh(x);
  return bytes;
}
static uint64_t valid_size(const std::string& scenario){return (scenario=="low-yield"?80:5)*MiB;}
static void verify_candidates(ZenFS& fs,const std::string& scenario) {
  FileOptions options;options.use_direct_reads=true;Buffer expected,scratch;
  for(unsigned i=0;i<2;++i) {
    expected.fill(i+1);const auto size=i?valid_size(scenario):4*MiB;
    std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile(name(i),options,&reader,nullptr));
    for(uint64_t pos=0;pos<size;pos+=MiB) {
      Slice result;check(reader->Read(pos,MiB,IOOptions(),&result,scratch.get(),nullptr));
      require(result.size()==MiB && !std::memcmp(result.data(),expected.get(),MiB),"候选完整内容不符");
    }
  }
}
static void run_case(ZenFS& fs,ZonedBlockDevice& d,std::shared_ptr<Logger> logger,
                     const std::string& scenario,const std::string& out) {
  const auto initial=strict_stats();require(!d.GetUsedSpace(),"初始主机有效量非零");
  check(fs.CreateDir("/v1-execution-smoke",IOOptions(),nullptr));FileOptions options;
  options.use_direct_reads=options.use_direct_writes=true;Buffer data,scratch;
  for(unsigned i=0;i<N-2;++i)write(fs,"/v1-execution-smoke/filler"+std::to_string(i)+".bin",Z,100+i,Env::WLTH_MEDIUM,data,options);
  uint64_t sources[2];
  for(unsigned i=0;i<2;++i) {
    const auto bytes=i?valid_size(scenario):4*MiB;
    write(fs,name(i),bytes,i+1,Env::WLTH_LONG,data,options);sources[i]=extent(fs,name(i),bytes).zone_start;
    std::unique_ptr<FSWritableFile> guard;check(fs.ReopenWritableFile(name(i),options,&guard,nullptr));
    write(fs,name(i,true),Z-bytes,i+11,Env::WLTH_MEDIUM,data,options);
    require(extent(fs,name(i,true),Z-bytes).zone_start==sources[i] && d.GetIOZone(sources[i])->IsFull(),"有效和退役文件没有同Zone");
    check(fs.DeleteFile(name(i,true),IOOptions(),nullptr));check(guard->Close(IOOptions(),nullptr));
  }
  require(sources[0]!=sources[1],"两个候选同区");
  // 保留与旧受控实验相同的空间布局及自然GC节拍，不直接调用维护函数。
  require(100*d.GetFreeSpace()/(d.GetFreeSpace()+d.GetUsedSpace()+d.GetReclaimableSpace())==24,"准备空间不是24%");
  std::this_thread::sleep_for(std::chrono::seconds(33));
  std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile(name(1),options,&reader,nullptr));data.fill(2);
  auto read=[&](uint64_t offset) {
    Slice result;check(reader->Read(offset,4096,IOOptions(),&result,scratch.get(),nullptr));
    require(result.size()==4096 && !std::memcmp(result.data(),data.get()+offset%MiB,4096),"受控页内容错误");
  };
  // 通过真实读取与C4确认两个不同物理块，不把相邻逻辑页当成不同物理块。
  const auto empty=all(d,sources[1]);
  require(std::all_of(empty.blocks.begin(),empty.blocks.end(),[](const auto& b){return !b.rc;}),"初始物理块RC不是零");
  read(0);const auto one=all(d,sources[1]);unsigned cold=0;
  while(cold<one.blocks.size() && !one.blocks[cold].rc)++cold;
  require(cold<one.blocks.size(),"首页没有产生真实NAND读取");
  uint64_t hot_offset=0;unsigned hot=0;
  for(uint64_t pos=4096;pos<MiB;pos+=4096) {
    read(pos);const auto report=all(d,sources[1]);
    for(const auto& b:report.blocks)if(b.block_id!=cold && b.rc){hot_offset=pos;hot=b.block_id;break;}
    if(hot_offset)break;
  }
  require(hot_offset,"不能找到同Zone第二个物理Block");
  // 等到下一轮原GC检查后开始有限输入轨迹；定时仅用于功能因果对照，不报告限速性能。
  logger->Flush();const auto checks=gc_checks(out+"/zenfs.run.log");const auto limit=Clock::now()+std::chrono::seconds(15);
  while(gc_checks(out+"/zenfs.run.log")==checks) {
    require(Clock::now()<limit,"无法观察原GC检查节拍");std::this_thread::sleep_for(std::chrono::milliseconds(50));logger->Flush();
  }
  std::this_thread::sleep_for(std::chrono::seconds(4));
  auto frame=all(d,sources[1]);const uint64_t cold_seed=scenario=="low-yield"?800:980;
  const uint64_t hot_seed=scenario=="low-yield"?128:600;
  for(uint64_t n=frame.blocks[cold].rc;n<cold_seed;++n)read(0);
  frame=all(d,sources[1]);for(uint64_t n=frame.blocks[hot].rc;n<hot_seed;++n)read(hot_offset);
  frame=all(d,sources[1]);
  require(frame.blocks[cold].rc==cold_seed && frame.blocks[hot].rc==hot_seed,"预读计数不符");
  if(scenario=="stopped")std::this_thread::sleep_for(std::chrono::seconds(11));
  for(unsigned i=N-2;i<N;++i)write(fs,"/v1-execution-smoke/filler"+std::to_string(i)+".bin",i+1==N?Z/2:Z,100+i,Env::WLTH_MEDIUM,data,options);
  const auto free=d.GetFreeSpace(),nonfree=d.GetUsedSpace()+d.GetReclaimableSpace();
  require(100*free/(free+nonfree)==18,"GC触发前空闲不是18%");
  data.fill(2);std::ofstream input(out+"/输入与映射.csv");
  input<<"请求,逻辑偏移,相对纳秒,当前映射Zone\n";
  const auto start=Clock::now();const unsigned requests=scenario=="stopped"?0:scenario=="low-yield"?960:448;
  Info(logger,"全块对照起点：场景=%s，冷候选=%lu，热候选=%lu，冷块=%u，冷块RC=%lu，热块=%u，热块RC=%lu，热偏移=%lu，后续请求=%u，空闲=%lu，非空闲=%lu",
      scenario.c_str(),sources[0],sources[1],cold,cold_seed,hot,hot_seed,hot_offset,requests,free,nonfree);
  for(unsigned n=0;n<requests;++n) {
    std::this_thread::sleep_until(start+std::chrono::nanoseconds(uint64_t(n)*1000000000/64));read(hot_offset);
    input<<n<<','<<hot_offset<<','<<std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-start).count()<<','
      <<extent(fs,name(1),valid_size(scenario)).zone_start<<'\n';
  }
  std::this_thread::sleep_until(start+std::chrono::seconds(25));reader.reset();logger->Flush();
  std::ofstream final(out+"/结束空间.csv");final<<"初始空闲,结束空闲,结束有效,结束可回收,初始触发数,结束触发数,历史最高RC\n";
  const auto counts=strict_stats();final<<free<<','<<d.GetFreeSpace()<<','<<d.GetUsedSpace()<<','<<d.GetReclaimableSpace()<<','
      <<initial[6]<<','<<counts[6]<<','<<counts[8]<<'\n';
  require(counts[8]<=1024,"严格RC超过阈值");
  std::ofstream resets(out+"/源区Reset核验.csv");resets<<"Zone,Empty,设备代际,RC总和,十秒增量总和,三十秒增量总和\n";
  for(const auto source:sources) {
    const auto report=all(d,source);uint64_t sum=0,d10=0,d30=0;
    for(const auto& b:report.blocks){sum+=b.rc;d10+=b.delta10;d30+=b.delta30;}
    const bool empty=d.GetIOZone(source)->IsEmpty();
    if(empty)require(!sum && !d10 && !d30,"源区Reset后残留全块计数或近期历史");
    resets<<source<<','<<empty<<','<<report.blocks[0].generation<<','<<sum<<','<<d10<<','<<d30<<'\n';
  }
  Info(logger,"全块业务计量截止：内容核验另计");logger->Flush();
  verify_candidates(fs,scenario);std::cout<<"全块受控功能对照通过："<<scenario<<std::endl;
}
int main(int argc,char** argv) {
  try {
    require(argc==3,"用法：程序 opportunity|stopped|low-yield 结果目录");
    const std::string scenario=argv[1],out=argv[2];
    require(scenario=="opportunity" || scenario=="stopped" || scenario=="low-yield","未知场景");
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(out+"/zenfs.run.log",&logger));logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);check(owned->Open(false,true));
    auto* device=owned.get();ZenFS fs(owned.release(),FileSystem::Default(),logger);check(fs.Mount(false));
    run_case(fs,*device,logger,scenario,out);return 0;
  }catch(const std::exception& e){std::cerr<<"全块受控功能对照失败："<<e.what()<<std::endl;return 1;}
}
