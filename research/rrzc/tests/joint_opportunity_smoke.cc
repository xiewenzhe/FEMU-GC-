// 联合维护受控对照：真实ZenFS读写及原维护线程，不注入RC或调用维护函数。
// 复用已验证的载荷/映射工具；不是RocksDB SST或YCSB性能负载。
#define main archived_joint_helpers_main
#include "v1_execution_smoke_test.cc"
#undef main
#ifdef RRZC_JOINT_TRACE_AVAILABLE
#include "joint_read_trace.h"
#endif

constexpr uint64_t JointZone=128*MiB, JointThreshold=1024, JointSeed=928;
constexpr unsigned JointFillers=22;
static std::string joint_file(unsigned i,bool dead=false) {
  return "/v1-execution-smoke/joint"+std::to_string(i)+(dead?".dead.sst":".live.sst");
}
static uint64_t joint_hot_bytes(const std::string& scenario) {
  return (scenario=="low-yield" ? 24 : scenario=="retire" ? 8 : scenario=="eligible-cost" ? 10 : 5)*MiB;
}
static rrzc_v1::RecentReport joint_recent(ZonedBlockDevice& d) {
  rrzc_v1::RecentReport r;check(d.GetZoneRecentReads(&r));
  require(r.threshold==JointThreshold && r.blocks_per_zone==128 && r.zones.size()==32,
          "不是本轮4GiB、128MiB区、RR1024受控设备");
  return r;
}
static void joint_verify(ZenFS& fs,const std::string& scenario) {
  FileOptions o;o.use_direct_reads=true;Buffer expected,scratch;
  for(unsigned i=0;i<JointFillers+2;++i) {
    const bool filler=i<JointFillers;const unsigned id=filler ? i : i-JointFillers;
    const auto name=filler ? "/v1-execution-smoke/filler"+std::to_string(i)+".bin" : joint_file(id);
    if(!filler && id==1 && scenario=="retire") {
      require(fs.FileExists(name,IOOptions(),nullptr).IsNotFound(),"预定退役文件仍存在");continue;
    }
    const uint64_t bytes=filler ? JointZone : id ? joint_hot_bytes(scenario) : 4*MiB;
    uint64_t size=0;check(fs.GetFileSize(name,IOOptions(),&size,nullptr));require(size==bytes,"校验长度错误");
    expected.fill(filler ? 100+i : id+1);
    std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile(name,o,&reader,nullptr));
    // 所有存活候选全量校验；填充文件首尾抽检，不能称全盘全量校验。
    for(uint64_t off=0;off<bytes;) {
      const size_t length=filler ? 4096 : MiB;Slice result;
      check(reader->Read(off,length,IOOptions(),&result,scratch.get(),nullptr));
      require(result.size()==length && !std::memcmp(result.data(),expected.get()+off%MiB,length),
              "载荷字节不符："+name+"，偏移="+std::to_string(off));
      if(!filler)off+=MiB;else if(!off)off=bytes-4096;else break;
    }
  }
  std::cout<<"只读校验通过：存活候选完整载荷，22个填充文件首尾抽检\n";
}
static void joint_run(ZenFS& fs,ZonedBlockDevice& d,std::shared_ptr<Logger> logger,
                      const std::string& scenario,const std::string& out) {
  require(d.IsContentGuardEnabled() && !d.GetUsedSpace(),"受控盘并非全空或内容隔离未启用");
  check(fs.CreateDir("/v1-execution-smoke",IOOptions(),nullptr));
  FileOptions o;o.use_direct_reads=o.use_direct_writes=true;Buffer data,scratch;
  for(unsigned i=0;i<JointFillers-2;++i) {
    write(fs,"/v1-execution-smoke/filler"+std::to_string(i)+".bin",JointZone,100+i,Env::WLTH_MEDIUM,data,o);
    if(i%8==7)std::cout<<"准备完成填充区="<<i+1<<std::endl;
  }
  uint64_t source[2]={};std::unique_ptr<FSWritableFile> guard[2];
  for(unsigned i=0;i<2;++i) {
    const uint64_t bytes=i ? joint_hot_bytes(scenario) : 4*MiB;
    write(fs,joint_file(i),bytes,i+1,Env::WLTH_LONG,data,o);
    source[i]=extent(fs,joint_file(i),bytes).zone_start;
    check(fs.ReopenWritableFile(joint_file(i),o,&guard[i],nullptr));
    write(fs,joint_file(i,true),JointZone-bytes,10+i,Env::WLTH_MEDIUM,data,o);
    require(extent(fs,joint_file(i,true),JointZone-bytes).zone_start==source[i] &&
            d.GetIOZone(source[i])->IsFull(),"有效和待退役载荷没有同区写满");
    check(fs.DeleteFile(joint_file(i,true),IOOptions(),nullptr));
  }
  require(source[0]!=source[1],"候选源区相同");
  // 准备阶段保留24%空闲，让原GC自然不触发；关闭句柄使满区留存历史真实可见。
  // 不能一直持写锁等待窗口，否则快照会跳过候选，模型永远拿不到连续历史。
  for(auto& g:guard){check(g->Close(IOOptions(),nullptr));g.reset();}
  const auto free=d.GetFreeSpace(),nonfree=d.GetUsedSpace()+d.GetReclaimableSpace();
  require(100*free/(free+nonfree)==24,"准备阶段未保持24%空闲，不修改GC条件补救");
  std::ofstream observations(out+"/recent.csv");
  observations<<"阶段,组号,设备会话高位,设备会话低位,Zone,设备代际,块编号,快照纳秒,代际起点纳秒,RC,十秒真实增量,三十秒真实增量,窗口标志\n";
  auto before=joint_recent(d);record(observations,"准备完成",before,source);
  require(!block(before,source[0]).rc && !block(before,source[1]).rc,"准备阶段有未计划候选读取");
  // 等待真实同代际窗口，不以qcow2代替模拟NAND或RC状态。
  auto deadline=Clock::now()+std::chrono::seconds(40);
  while((block(before,source[1]).flags&6)!=6) {
    require(Clock::now()<deadline,"近期窗口预热超时");
    std::this_thread::sleep_for(std::chrono::milliseconds(100));before=joint_recent(d);
  }
  // 设备窗口自启动起计时，不能把其30秒完整误当作文件已存在30秒。
  std::this_thread::sleep_for(std::chrono::seconds(33));
  // 默认关闭；只在独立概率采证入口开启，模型和原读取安排不接收轨迹结果。
#ifdef RRZC_JOINT_TRACE_AVAILABLE
  JointReadTrace trace(std::getenv("RRZC_JOINT_TRACE"),out,[&]{return joint_recent(d);});
  if (trace.Enabled()) Info(logger,"块轨迹对时：墙钟纳秒=%lu",JointReadTrace::WallNow());
#endif
  auto align=[&] {
    logger->Flush();const auto checks=gc_checks(out+"/zenfs.run.log");
    const auto limit=Clock::now()+std::chrono::seconds(15);
    while(gc_checks(out+"/zenfs.run.log")==checks) {
      require(Clock::now()<limit,"无法观察原GC节拍");
      std::this_thread::sleep_for(std::chrono::milliseconds(100));logger->Flush();
    }
  };
  align();std::this_thread::sleep_for(std::chrono::seconds(6));
  std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile(joint_file(1),o,&reader,nullptr));
  data.fill(2);const auto seed_start=Clock::now();
  for(uint64_t n=0;n<JointSeed;++n) {
    std::this_thread::sleep_until(seed_start+std::chrono::nanoseconds(n*1000000000ULL/1024));
    Slice result;check(reader->Read(0,4096,IOOptions(),&result,scratch.get(),nullptr));
    require(result.size()==4096 && !std::memcmp(result.data(),data.get(),4096),"预读字节错误");
  }
  require(Clock::now()-seed_start<std::chrono::seconds(2),"预读调度过慢，不修改条件继续");
  auto ready=joint_recent(d);
  require(block(ready,source[1]).rc==JointSeed && block(ready,source[1]).delta10==JointSeed &&
          block(ready,source[1]).generation==block(before,source[1]).generation &&
          block(ready,source[1]).block_id==block(before,source[1]).block_id,"预读同块RC或代际不符");
  if(scenario=="stopped") {
    std::this_thread::sleep_for(std::chrono::seconds(11));align();
    std::this_thread::sleep_for(std::chrono::seconds(7));ready=joint_recent(d);
    require(block(ready,source[1]).rc==JointSeed && !block(ready,source[1]).delta10,"热读停止场景仍有近期读数");
  }
  // 真实写入最后两份填充文件，将空闲从24%降至17%，由原线程自然触发GC。
  for(unsigned i=JointFillers-2;i<JointFillers;++i)
    write(fs,"/v1-execution-smoke/filler"+std::to_string(i)+".bin",JointZone,100+i,Env::WLTH_MEDIUM,data,o);
  data.fill(2);ready=joint_recent(d);
  const auto current_free=d.GetFreeSpace(),current_nonfree=d.GetUsedSpace()+d.GetReclaimableSpace();
  require(100*current_free/(current_free+current_nonfree)==17 &&
      block(ready,source[1]).rc==JointSeed && !block(ready,source[0]).rc,
      "触发前初始空间或真实RC已变化，拒绝继续");
  record(observations,"释放前",ready,source);
  std::ofstream initial(out+"/initial.csv");
  initial<<"组号,源Zone,有效字节,RC,十秒真实增量,三十秒真实增量,块编号,代际,空闲百分比\n";
  for(unsigned i=0;i<2;++i) {
    const auto b=block(ready,source[i]);
    initial<<i<<','<<source[i]<<','<<(i?joint_hot_bytes(scenario):4*MiB)<<','<<b.rc<<','<<b.delta10<<','
        <<b.delta30<<','<<b.block_id<<','<<b.generation<<",17\n";
  }
  initial.close();require(initial.good(),"初始状态写入失败");
  // 新短窗停止场景不再等待11秒：保留十秒旧热度，但后续不再读取。
  const unsigned requests=(scenario=="stopped" || scenario=="burst-stopped") ? 0 : scenario=="retire" ? 64 : 128;
  Info(logger,"联合维护释放：场景=%s，冷源=%lu，热源=%lu，冷有效=%lu，热有效=%lu，RC=%lu，后续读每秒=16，计划读取=%u，观察纳秒=25000000000，退役纳秒=%lu",
       scenario.c_str(),source[0],source[1],4*MiB,joint_hot_bytes(scenario),JointSeed,
       requests,uint64_t(scenario=="retire"?4000000000ULL:0ULL));
  const auto start=Clock::now();
  std::ofstream reads(out+"/reads.csv");reads<<"序号,计划纳秒,实际开始纳秒,API耗时纳秒\n";
  bool retired=false;
  for(unsigned n=0;n<requests;++n) {
    std::this_thread::sleep_until(start+std::chrono::nanoseconds(uint64_t(n)*1000000000ULL/16));
    const auto begin=Clock::now();Slice result;
    check(reader->Read(0,4096,IOOptions(),&result,scratch.get(),nullptr));
    reads<<n<<','<<uint64_t(n)*1000000000ULL/16<<','
         <<std::chrono::duration_cast<std::chrono::nanoseconds>(begin-start).count()<<','
         <<std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-begin).count()<<'\n';
    require(result.size()==4096 && !std::memcmp(result.data(),data.get(),4096),"固定后续读数据不符");
  }
  if(scenario=="retire") {
    std::this_thread::sleep_until(start+std::chrono::seconds(4));reader.reset();
    check(fs.DeleteFile(joint_file(1),IOOptions(),nullptr));retired=true;
    Info(logger,"联合维护预定退役：实际纳秒=%ld",static_cast<long>(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-start).count()));
  }
  std::this_thread::sleep_until(start+std::chrono::seconds(25));reader.reset();reads.close();
  require(reads.good(),"读取轨迹写入失败");logger->Flush();
  record(observations,"观察结束",joint_recent(d),source);
#ifdef RRZC_JOINT_TRACE_AVAILABLE
  trace.Close();
#endif
  Info(logger,"联合维护观察结束：场景=%s，实际读取=%u，已退役=%d；维护次数只从真实完成记录核验",
       scenario.c_str(),requests,retired);
  std::cout<<"观察完成："<<scenario<<"，真实后续读取="<<requests<<std::endl;
}
int main(int argc,char** argv) {
  try {
    require(argc==4,"用法：程序 run|verify 场景 新结果目录");
    const std::string stage=argv[1],scenario=argv[2],out=argv[3];const bool readonly=stage=="verify";
    require((readonly || stage=="run") && (scenario=="opportunity" || scenario=="low-yield" ||
        scenario=="stopped" || scenario=="retire" || scenario=="burst-stopped" || scenario=="eligible-cost") &&
        out=="/home/femu/rrzc-joint-results/"+scenario,
        "场景或隔离目录不符");
    const auto log=out+"/zenfs."+stage+".log";require(!std::filesystem::exists(log),"拒绝覆盖原始日志");
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(log,&logger));logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);check(owned->Open(readonly,true));
    auto* d=owned.get();auto fs=std::make_unique<ZenFS>(owned.release(),FileSystem::Default(),logger);check(fs->Mount(readonly));
    if(readonly)joint_verify(*fs,scenario);else joint_run(*fs,*d,logger,scenario,out);
    return 0;
  }catch(const std::exception& error){std::cerr<<"联合维护短测失败："<<error.what()<<std::endl;return 1;}
}
