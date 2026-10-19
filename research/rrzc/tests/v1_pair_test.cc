// 独立受控配对短测；载荷.sst不是RocksDB生成的SST，不作为稳态性能结论。
// 只用正常文件读写/删除，依靠原维护线程；不直接调用GC、RR、Finish或Reset。
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <sys/resource.h>
#include <algorithm>
#include <vector>
#include "logging/logging.h"
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "plugin/zenfs/fs/snapshot.h"

using namespace rocksdb;
using Clock=std::chrono::steady_clock;
constexpr uint64_t MiB=1ULL<<20, ZoneBytes=256*MiB, Reads=4032;
constexpr unsigned Fillers=24;
static void require(bool ok,const std::string& why) {
  if(!ok) throw std::runtime_error(why);
}
template<class S> static void check(const S& s) { require(s.ok(),s.ToString()); }
static std::string file(unsigned i,bool dead=false) {
  return "/v1-execution-smoke/"+std::string(i?"hot":"cold")+(dead?".dead.bin":".live.sst");
}
static unsigned char pattern(uint64_t off,unsigned seed) {
  return ((off%MiB)*131+17+seed*31)%251;
}
struct Buffer {
  std::unique_ptr<char,decltype(&std::free)> data{nullptr,&std::free};
  Buffer() { void* p=nullptr; require(posix_memalign(&p,4096,MiB)==0,"对齐缓冲申请失败"); data.reset(static_cast<char*>(p)); }
  char* get() { return data.get(); }
  void fill(unsigned seed) { for(uint64_t j=0;j<MiB;++j) get()[j]=pattern(j,seed); }
};
static std::optional<ZoneExtentSnapshot> find(ZenFS& fs,const std::string& name) {
  ZenFSSnapshot s; ZenFSSnapshotOptions o; o.zone_=o.zone_file_=true;
  fs.GetZenFSSnapshot(s,o); std::optional<ZoneExtentSnapshot> out;
  for(const auto& e:s.extents_) if(e.filename==name) {
    require(!out,"功能载荷不是单片段映射"); out=e;
  }
  return out;
}
static ZoneExtentSnapshot extent(ZenFS& fs,const std::string& name,uint64_t bytes) {
  auto e=find(fs,name); require(e&&e->length==bytes,"功能载荷映射缺失或长度不符"); return *e;
}
static void write(ZenFS& fs,const std::string& name,uint64_t bytes,unsigned seed,
                  Env::WriteLifeTimeHint hint,Buffer& b,const FileOptions& o) {
  require(fs.FileExists(name,IOOptions(),nullptr).IsNotFound(),"拒绝覆盖已有文件");
  b.fill(seed); std::unique_ptr<FSWritableFile> w;
  check(fs.NewWritableFile(name,o,&w,nullptr)); w->SetWriteLifeTimeHint(hint);
  for(uint64_t off=0;off<bytes;off+=MiB) check(w->Append(Slice(b.get(),MiB),IOOptions(),nullptr));
  check(w->Close(IOOptions(),nullptr));
}
static rrzc_v1::RecentReport recent(ZonedBlockDevice& d) {
  rrzc_v1::RecentReport r; check(d.GetZoneRecentReads(&r));
  require(r.threshold==4096&&r.blocks_per_zone==64&&r.zones.size()==32,"设备几何或RR阈值不符"); return r;
}
static rrzc_v1::RecentBlock block(const rrzc_v1::RecentReport& r,uint64_t source) {
  for(const auto& b:r.zones) if(b.zone==source) return b;
  throw std::runtime_error("C2h缺少功能源区");
}
static void record(std::ofstream& out,const char* phase,const rrzc_v1::RecentReport& r,
                   const uint64_t sources[2]) {
  for(unsigned i=0;i<2;++i) {
    auto b=block(r,sources[i]);
    out<<phase<<','<<i<<','<<r.session_hi<<','<<r.session_lo<<','<<b.zone<<','<<b.generation<<','
       <<b.block_id<<','<<b.snapshot_ns<<','<<b.epoch_start_ns<<','<<b.rc<<','<<b.delta10<<','
       <<b.delta30<<','<<b.flags<<'\n';
  }
  out.flush(); require(out.good(),"逐块功能记录写入失败");
}
static unsigned gc_checks(const std::string& log) {
  std::ifstream in(log); require(in.good(),"不能读取本轮维护日志");
  unsigned n=0; std::string line;
  while(std::getline(in,line)) if(line.find("GC 检查：")!=std::string::npos) ++n;
  return n;
}
struct Event { uint64_t due; unsigned kind,index; };
static std::vector<Event> plan(const std::string& profile) {
  std::vector<Event> events;
  unsigned count=profile=="continuous"?768:8;
  for(unsigned i=0;i<count;++i) events.push_back({i*62500000ULL,0,i});
  if(profile=="retire") events.push_back({5*1000000000ULL,4,0});
  events.push_back({32*1000000000ULL,1,0});
  for(unsigned i=0;i<8;++i) events.push_back({32*1000000000ULL+i*125000000ULL,2,i});
  events.push_back({33*1000000000ULL,3,0});
  std::sort(events.begin(),events.end(),[](const Event& a,const Event& b) {
    return a.due<b.due || (a.due==b.due && a.kind<b.kind);
  });
  return events;
}
static void run(ZenFS& fs,ZonedBlockDevice& d,std::shared_ptr<Logger> logger,
                const std::string& out,const std::string& profile) {
  require(d.GetUsedSpace()==0,"功能设备已有有效数据，拒绝混入旧载荷");
  check(fs.CreateDir("/v1-execution-smoke",IOOptions(),nullptr));
  FileOptions o; o.use_direct_reads=o.use_direct_writes=true; Buffer data,scratch;
  for(unsigned i=0;i<Fillers;++i) {
    write(fs,"/v1-execution-smoke/filler"+std::to_string(i)+".bin",ZoneBytes,100+i,Env::WLTH_MEDIUM,data,o);
    if(i%8==7) std::cout<<"功能准备：已写满"<<i+1<<"/24个填充区"<<std::endl;
  }
  uint64_t sources[2]={},host_generation[2]={};
  std::unique_ptr<FSWritableFile> guards[2];
  for(unsigned i=0;i<2;++i) {
    uint64_t bytes=(i?32:16)*MiB;
    write(fs,file(i),bytes,i+1,Env::WLTH_LONG,data,o);
    auto live=extent(fs,file(i),bytes); sources[i]=live.zone_start;
    require(live.start==sources[i],"有效载荷不在源区起点");
    // 正常写句柄持写锁，仅在准备阶段阻止不完整快照参与维护。
    check(fs.ReopenWritableFile(file(i),o,&guards[i],nullptr));
    write(fs,file(i,true),ZoneBytes-bytes,i+10,Env::WLTH_MEDIUM,data,o);
    auto dead=extent(fs,file(i,true),ZoneBytes-bytes);
    require(dead.zone_start==sources[i]&&dead.start==sources[i]+bytes&&d.GetIOZone(sources[i])->IsFull(),
            "真实填充未形成有效/无效同区布局");
    check(fs.DeleteFile(file(i,true),IOOptions(),nullptr));
    require(d.GetIOZone(sources[i])->used_capacity_==bytes,"删除后有效量不符");
    host_generation[i]=d.GetIOZone(sources[i])->GetResetState().generation;
  }
  require(sources[0]!=sources[1],"冷热载荷未分区");
  uint64_t free=d.GetFreeSpace(),percent=100*free/(free+d.GetUsedSpace()+d.GetReclaimableSpace());
  require(percent==10,"功能准备未形成原始10%空闲，不调整GC门槛补救");
  std::ofstream initial(out+"/initial.csv"), observations(out+"/recent.csv");
  require(initial.good()&&observations.good(),"功能记录创建失败");
  observations<<"阶段,组号,设备会话高位,设备会话低位,Zone,设备代际,块编号,快照纳秒,代际起点纳秒,RC,十秒真实增量,三十秒真实增量,窗口标志\n";
  auto before=recent(d);
  initial<<"组号,文件,源Zone,有效字节,主机代际,设备代际,初始RC,空闲比例\n";
  for(unsigned i=0;i<2;++i) {
    auto b=block(before,sources[i]); require(b.rc==0,"准备阶段已有未计划读取");
    initial<<i<<','<<file(i)<<','<<sources[i]<<','<<(i?32:16)*MiB<<','<<host_generation[i]<<','<<b.generation<<",0,"<<percent<<'\n';
  }
  initial.close();
  // 窗口预热只等真实时间，不伪造累计RC或近期增量。
  auto limit=Clock::now()+std::chrono::seconds(40);
  while((block(before,sources[0]).flags&6)!=6||(block(before,sources[1]).flags&6)!=6) {
    require(Clock::now()<limit,"真实窗口预热超时");
    std::this_thread::sleep_for(std::chrono::milliseconds(100)); before=recent(d);
  }
  logger->Flush(); unsigned previous=gc_checks(out+"/zenfs.run.log");
  limit=Clock::now()+std::chrono::seconds(15);
  // 只观察原GC节拍，避免功能输入恰好在十秒窗口边界过期；不调用或唤醒GC。
  do {
    require(Clock::now()<limit,"未观察到原GC检查节拍");
    std::this_thread::sleep_for(std::chrono::milliseconds(100)); logger->Flush();
  } while(gc_checks(out+"/zenfs.run.log")==previous);
  // 两组共用固定七秒相位；释放后先给原ZC一次机会，再由后续真实读取跨阈值。
  std::this_thread::sleep_for(std::chrono::seconds(7));
  before=recent(d); record(observations,"热读前",before,sources);
  require(block(before,sources[1]).rc==0,"热读前RC已变化");
  std::unique_ptr<FSRandomAccessFile> reader;
  check(fs.NewRandomAccessFile(file(1),o,&reader,nullptr)); data.fill(2);
  std::ofstream reads(out+"/reads.csv"); require(reads.good(),"读取记录创建失败");
  reads<<"序号,开始纳秒,API耗时纳秒,字节\n";
  auto start=Clock::now(); Info(logger,"V1功能热读：开始，计划4032次真实4KiB读取，不注入计数");
  for(uint64_t i=0;i<Reads;++i) {
    std::this_thread::sleep_until(start+std::chrono::nanoseconds(i*1000000000ULL/4096));
    Slice result; auto begin=Clock::now();
    check(reader->Read(0,4096,IOOptions(),&result,scratch.get(),nullptr)); auto end=Clock::now();
    require(result.size()==4096&&std::memcmp(result.data(),data.get(),4096)==0,"功能读取字节不符");
    reads<<i<<','<<std::chrono::duration_cast<std::chrono::nanoseconds>(begin-start).count()<<','
         <<std::chrono::duration_cast<std::chrono::nanoseconds>(end-begin).count()<<",4096\n";
  }
  reads.close(); reader.reset(); require(reads.good(),"实际读取记录写入失败");
  require(Clock::now()-start<std::chrono::seconds(2),"功能读输入发送过慢，拒绝继续而不改发送计划");
  auto ready=recent(d); record(observations,"释放前",ready,sources);
  auto hot=block(ready,sources[1]);
  require(hot.rc==Reads&&hot.delta10==Reads&&hot.delta30==Reads&&hot.flags==7&&
          hot.generation==block(before,sources[1]).generation&&hot.block_id==block(before,sources[1]).block_id,
          "真实同块读取及近期统计未达到固定功能条件");
  require(block(ready,sources[0]).rc==0,"冷区出现未计划读取");
  Info(logger,"V1功能准备：原GC20%%、RR4096不变，冷区=%lu有效16MiB，热区=%lu有效32MiB，真实RC=%lu，即将释放两个准备句柄",
       sources[0],sources[1],hot.rc);
  for(auto& guard:guards) { check(guard->Close(IOOptions(),nullptr)); guard.reset(); }
  // 未来计划只用于负载发送，不传给V1；不得按选区结果调整次数或退役时间。
  const auto events=plan(profile);
  std::ofstream scheduled(out+"/plan.csv"),ops(out+"/operations.csv");
  require(scheduled.good()&&ops.good(),"业务计划或操作日志创建失败");
  scheduled<<"顺序,计划纳秒,类型,序号\n";
  for(size_t i=0;i<events.size();++i) scheduled<<i<<','<<events[i].due<<','<<events[i].kind<<','<<events[i].index<<'\n';
  scheduled.close(); require(scheduled.good(),"业务计划保存失败");
  ops<<"顺序,计划纳秒,类型,序号,开始纳秒,API耗时纳秒,迟到纳秒,字节,成功\n";
  check(fs.NewRandomAccessFile(file(1),o,&reader,nullptr));
  std::unique_ptr<FSWritableFile> writer;
  unsigned completed_reads=0;
  Buffer business_write; business_write.fill(200); data.fill(2);
  auto origin=Clock::now();
  Info(logger,"V1配对业务：开始，场景=%s，观察秒数=60，未来计划仅负载可见",profile.c_str());
  auto ns=[](Clock::duration d) {return std::chrono::duration_cast<std::chrono::nanoseconds>(d).count();};
  for(size_t i=0;i<events.size();++i) {
    const auto& e=events[i]; auto due=origin+std::chrono::nanoseconds(e.due);
    std::this_thread::sleep_until(due);
    // 两个字节模式在业务阶段之前准备；只计时真实FS调用。
    auto begin=Clock::now(); IOStatus s; Slice result;
    uint64_t bytes=0;
    if(e.kind==0) { bytes=4096; s=reader->Read(0,4096,IOOptions(),&result,scratch.get(),nullptr); }
    else if(e.kind==1) s=fs.NewWritableFile("/v1-execution-smoke/update.bin",o,&writer,nullptr);
    else if(e.kind==2) { bytes=MiB; s=writer->Append(Slice(business_write.get(),MiB),IOOptions(),nullptr); }
    else if(e.kind==3) s=writer->Close(IOOptions(),nullptr);
    else s=fs.DeleteFile(file(1),IOOptions(),nullptr);
    auto end=Clock::now();
    ops<<i<<','<<e.due<<','<<e.kind<<','<<e.index<<','<<ns(begin-origin)<<','<<ns(end-begin)<<','
       <<(begin>due?ns(begin-due):0)<<','<<bytes<<','<<s.ok()<<'\n'; ops.flush(); check(s);
    if(e.kind==0) {
      require(result.size()==4096&&std::memcmp(result.data(),data.get(),4096)==0,"未来业务读取字节不符");
      if(++completed_reads==(profile=="continuous"?768U:8U)) reader.reset();
    }
    if(e.kind==1) writer->SetWriteLifeTimeHint(Env::WLTH_MEDIUM);
    if(e.kind==3) writer.reset();
    if(e.kind==4) Info(logger,"V1配对退役：经过纳秒=%ld，热文件正常删除完成",ns(end-origin));
  }
  std::this_thread::sleep_until(origin+std::chrono::seconds(60));
  Info(logger,"V1配对业务：固定观察结束，经过纳秒=%ld，实际未来读取=%u，追加业务写入=8388608",ns(Clock::now()-origin),completed_reads);
  ops.close(); require(ops.good(),"业务操作日志写入失败");
  auto after=recent(d); record(observations,"观察结束",after,sources);
  require(after.session_hi==ready.session_hi&&after.session_lo==ready.session_lo,"设备会话改变");
  std::ofstream completed(out+"/completion.csv"); require(completed.good(),"完成记录创建失败");
  completed<<"组号,文件,源Zone,当前Zone,有效字节,源主机代际,源设备代际,源RC\n";
  for(unsigned i=0;i<2;++i) {
    auto e=find(fs,file(i));
    auto b=block(after,sources[i]);
    require(e.has_value() || (i==1&&profile=="retire"),"观察结束时非计划文件缺失");
    completed<<i<<','<<file(i)<<','<<sources[i]<<','<<(e?e->zone_start:0)<<','<<(e?e->length:0)<<','
             <<d.GetIOZone(sources[i])->GetResetState().generation<<','<<b.generation<<','<<b.rc<<'\n';
  }
  completed.close(); require(completed.good(),"完成记录写入失败");
  std::cout<<"配对固定业务完成：场景="<<profile<<"，观察60秒，未来读="<<completed_reads<<"，业务写8MiB；结果由独立日志核验"<<std::endl;
}
static void verify(ZenFS& fs,const std::string& out,const std::string& profile) {
  FileOptions o; o.use_direct_reads=true; Buffer expected,scratch;
  for(unsigned i=0;i<Fillers+2;++i) {
    if(i==1&&profile=="retire") {
      require(fs.FileExists(file(1),IOOptions(),nullptr).IsNotFound(),"计划退役文件仍存在"); continue;
    }
    bool filler=i>=2; unsigned index=filler?i-2:0;
    auto name=filler?"/v1-execution-smoke/filler"+std::to_string(index)+".bin":file(i);
    uint64_t bytes=filler?ZoneBytes:(i?32:16)*MiB,size=0;
    unsigned seed=filler?100+index:i+1;
    check(fs.GetFileSize(name,IOOptions(),&size,nullptr)); require(size==bytes,"只读校验长度不符");
    expected.fill(seed); std::unique_ptr<FSRandomAccessFile> r;
    check(fs.NewRandomAccessFile(name,o,&r,nullptr));
    for(uint64_t off=0;off<bytes;) {
      size_t n=filler?4096:MiB; Slice result;
      check(r->Read(off,n,IOOptions(),&result,scratch.get(),nullptr));
      require(result.size()==n&&std::memcmp(result.data(),expected.get()+off%MiB,n)==0,"只读数据比对失败");
      if(!filler) off+=MiB; else if(off==0) off=bytes-4096; else break;
    }
  }
  expected.fill(200);
  std::unique_ptr<FSRandomAccessFile> update;
  check(fs.NewRandomAccessFile("/v1-execution-smoke/update.bin",o,&update,nullptr));
  uint64_t size=0; check(fs.GetFileSize("/v1-execution-smoke/update.bin",IOOptions(),&size,nullptr));
  require(size==8*MiB,"追加业务文件长度不符");
  for(unsigned i=0;i<8;++i) {
    Slice result; check(update->Read(i*MiB,MiB,IOOptions(),&result,scratch.get(),nullptr));
    require(result.size()==MiB&&std::memcmp(result.data(),expected.get(),MiB)==0,"追加写入数据比对失败");
  }
  for(unsigned i=0;i<2;++i) require(fs.FileExists(file(i,true),IOOptions(),nullptr).IsNotFound(),"已删除的无效载荷仍存在");
  uint64_t verified=(profile=="retire"?24:56)*MiB;
  std::ofstream report(out+"/verify.csv"); report<<"完整有效数据字节,填充文件数,每填充文件抽检首尾字节\n"<<verified<<",24,4096\n";
  report.close(); require(report.good(),"只读验证记录写入失败");
  std::cout<<"只读校验通过：完整比对"<<verified/MiB<<"MiB有效数据，24个填充文件各抽检首尾4KiB；未启动维护线程"<<std::endl;
}
int main(int argc,char** argv) {
  try {
    require(argc==5,"用法：程序 run|verify greedy|v1 continuous|stop|retire 本轮独立结果目录");
    std::string mode=argv[1],strategy=argv[2],profile=argv[3],out=argv[4]; bool readonly=mode=="verify";
    require(mode=="run"||readonly,"模式非法");
    require(strategy=="greedy"||strategy=="v1","策略非法");
    require(profile=="continuous"||profile=="stop"||profile=="retire","场景非法");
    require(out=="/home/femu/rrzc-v1-pair-results/v1-pair-"+profile+"-"+strategy+"-20261014"&&std::filesystem::is_directory(out),"不是本轮独立结果目录");
    // 仅限制本测试进程的普通输出文件，防止失败时重复日志填满客机OS盘。
    const rlimit log_limit={64*MiB,64*MiB};
    require(setrlimit(RLIMIT_FSIZE,&log_limit)==0,"不能设置本测试文件大小上限");
    require(getenv("ZENFS_RR_ENABLE")&&std::string(getenv("ZENFS_RR_ENABLE"))==(readonly?"0":"1"),"RR开关与阶段不符");
    require(!getenv("ZENFS_ZC_TEST_ENABLE")&&!getenv("ZENFS_ZC_TEST_SOURCE")&&!getenv("ZENFS_RRZC_MODE"),"禁止人工选区或旧策略");
    if(!readonly) {
      require(strategy=="v1" ?
        (getenv("ZENFS_RRZC_V1_ENABLE")&&std::string(getenv("ZENFS_RRZC_V1_ENABLE"))=="1"&&!getenv("ZENFS_RRZC_V1_SHADOW")) :
        (getenv("ZENFS_RRZC_V1_SHADOW")&&std::string(getenv("ZENFS_RRZC_V1_SHADOW"))=="1"&&!getenv("ZENFS_RRZC_V1_ENABLE")),"实际/影子入口与策略不符");
      require(getenv("ZENFS_RRZC_SIMPLE_MODE")&&std::string(getenv("ZENFS_RRZC_SIMPLE_MODE"))=="valid"&&
        getenv("ZENFS_RRZC_RECLAIM_BYTES")&&std::string(getenv("ZENFS_RRZC_RECLAIM_BYTES"))=="201326592","真实选区或回收预算入口不符");
    } else require(!getenv("ZENFS_RRZC_V1_ENABLE")&&!getenv("ZENFS_RRZC_V1_SHADOW")&&!getenv("ZENFS_RRZC_SIMPLE_MODE"),"只读阶段禁止选区入口");
    std::string log=out+"/zenfs."+mode+".log"; require(!std::filesystem::exists(log),"拒绝覆盖日志");
    std::shared_ptr<Logger> logger; check(Env::Default()->NewLogger(log,&logger)); logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger); check(owned->Open(readonly,true));
    auto* d=owned.get(); auto fs=std::make_unique<ZenFS>(owned.release(),FileSystem::Default(),logger); check(fs->Mount(readonly));
    if(readonly) verify(*fs,out,profile); else run(*fs,*d,logger,out,profile);
    return 0;
  } catch(const std::exception& e) { std::cerr<<"V1配对短测失败："<<e.what()<<std::endl; return 1; }
}
