// 独立持续读尾部测试；复用旧助手但不修改旧载荷、入口或生产代码。
#define main archived_smoke_main
#include "v1_execution_smoke_test.cc"
#undef main

static const std::string Seed="/v1-execution-smoke/reserve.seed.sst";
static const std::string SeedDead="/v1-execution-smoke/reserve.seed.dead.bin";

static rrzc_cost::ReserveObservation reserve_observation(ZonedBlockDevice& d) {
  auto limit=Clock::now()+std::chrono::seconds(2);
  do {
    auto r=d.GetMigrationReserveObservation();
    if(r.known) return r;
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  } while(Clock::now()<limit);
  throw std::runtime_error("预留只读快照始终未知，不修改资源锁绕过");
}
static void save_state(ZenFS& fs,ZonedBlockDevice& d,std::ofstream& out,
                       const char* phase,uint64_t elapsed) {
  auto r=reserve_observation(d);
  std::optional<ZoneExtentSnapshot> extents[3];
  const std::string names[3]={Seed,file(0),file(1)};
  auto limit=Clock::now()+std::chrono::seconds(2);
  do {
    for(unsigned i=0;i<3;++i) extents[i]=find(fs,names[i]);
    if(extents[0]&&extents[1]&&extents[2]) break;
    require(Clock::now()<limit,"阶段快照持续缺少有效文件");
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    r=reserve_observation(d);
  } while(true);
  out<<phase<<','<<elapsed<<','<<r.present<<','<<r.zone<<','<<r.remaining<<','<<r.valid<<','
     <<r.wp<<','<<r.host_generation<<','<<d.GetFreeSpace()<<','<<d.GetUsedSpace()<<','
     <<d.GetReclaimableSpace();
  for(const auto& e:extents) {
    out<<','<<e->zone_start<<','<<e->start<<','<<e->length;
  }
  out<<'\n'; out.flush(); require(out.good(),"阶段记录写入失败");
}
static void fixed_reads(ZenFS& fs,const std::string& name,unsigned seed,uint64_t count,
                        const std::string& csv) {
  FileOptions o; o.use_direct_reads=true; Buffer expected,scratch; expected.fill(seed);
  std::unique_ptr<FSRandomAccessFile> r; check(fs.NewRandomAccessFile(name,o,&r,nullptr));
  std::ofstream out(csv); require(out.good(),"真实读记录创建失败");
  out<<"序号,计划纳秒,开始纳秒,API耗时纳秒,字节\n"; auto origin=Clock::now();
  for(uint64_t i=0;i<count;++i) {
    uint64_t due=i*1000000000ULL/4096;
    std::this_thread::sleep_until(origin+std::chrono::nanoseconds(due));
    Slice result; auto begin=Clock::now();
    check(r->Read(0,4096,IOOptions(),&result,scratch.get(),nullptr)); auto end=Clock::now();
    require(result.size()==4096&&std::memcmp(result.data(),expected.get(),4096)==0,"预读数据不符");
    out<<i<<','<<due<<','<<std::chrono::duration_cast<std::chrono::nanoseconds>(begin-origin).count()<<','
       <<std::chrono::duration_cast<std::chrono::nanoseconds>(end-begin).count()<<",4096\n";
  }
  require(Clock::now()-origin<std::chrono::seconds(2),"真实读发送过慢，保留结果，不调整速率");
  out.close(); require(out.good(),"真实读记录写入失败");
}
static void tail_run(ZenFS& fs,ZonedBlockDevice& d,std::shared_ptr<Logger> logger,
                     const std::string& out) {
  require(d.GetUsedSpace()==0,"设备并非全空，拒绝混入旧数据");
  check(fs.CreateDir("/v1-execution-smoke",IOOptions(),nullptr));
  FileOptions o; o.use_direct_reads=o.use_direct_writes=true; Buffer data;
  // 以正常文件I/O和真实RR形成尾部，不直接调用任何维护/迁移/Finish函数。
  write(fs,Seed,240*MiB,50,Env::WLTH_LONG,data,o);
  auto original=extent(fs,Seed,240*MiB);
  write(fs,SeedDead,16*MiB,51,Env::WLTH_MEDIUM,data,o);
  auto dead=extent(fs,SeedDead,16*MiB);
  require(dead.zone_start==original.zone_start&&dead.start==original.start+240*MiB&&
          d.GetIOZone(original.zone_start)->IsFull(),"预留种子未形成240/16MiB同区");
  check(fs.DeleteFile(SeedDead,IOOptions(),nullptr));
  fixed_reads(fs,Seed,50,4096,out+"/seed.reads.csv");
  auto limit=Clock::now()+std::chrono::seconds(30); bool moved=false;
  while(Clock::now()<limit) {
    // GetZenFSSnapshot会跳过持写锁的迁移中文件；暂时缺项不是映射丢失。
    auto e=find(fs,Seed); auto r=d.GetMigrationReserveObservation();
    if(e&&e->length==240*MiB&&e->zone_start!=original.zone_start&&d.GetIOZone(original.zone_start)->IsEmpty()&&
       r.known&&r.present&&r.zone==e->zone_start&&r.remaining==16*MiB&&r.valid==240*MiB) {
      moved=true; break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
  require(moved,"真实RR未形成16MiB预留尾部，不注入状态或调整规则");
  Info(logger,"尾部配对种子：真实RR已完成，源Zone=%lu，原有效量=251658240，当前预留尾部=16777216",original.zone_start);
  std::cout<<"准备完成：一次真实RR形成240MiB有效量、16MiB尾部"<<std::endl;
  for(unsigned i=0;i<Fillers;++i) {
    write(fs,"/v1-execution-smoke/filler"+std::to_string(i)+".bin",ZoneBytes,100+i,Env::WLTH_MEDIUM,data,o);
    if(i%8==7) std::cout<<"准备填充："<<i+1<<"/24"<<std::endl;
  }
  uint64_t sources[2]={}; std::unique_ptr<FSWritableFile> guards[2];
  for(unsigned i=0;i<2;++i) {
    uint64_t length=(i?32:16)*MiB;
    write(fs,file(i),length,i+1,Env::WLTH_LONG,data,o);
    auto e=extent(fs,file(i),length); sources[i]=e.zone_start;
    require(e.start==e.zone_start,"候选不在独立源区起点");
    check(fs.ReopenWritableFile(file(i),o,&guards[i],nullptr));
    write(fs,file(i,true),ZoneBytes-length,i+10,Env::WLTH_MEDIUM,data,o);
    auto tmp=extent(fs,file(i,true),ZoneBytes-length);
    require(tmp.zone_start==e.zone_start&&tmp.start==e.start+length&&d.GetIOZone(e.zone_start)->IsFull(),
            "候选未形成同区有效/无效布局");
    check(fs.DeleteFile(file(i,true),IOOptions(),nullptr));
  }
  require(sources[0]!=sources[1],"两个候选没有分区");
  auto r=reserve_observation(d);
  require(r.present&&r.remaining==16*MiB&&r.valid==240*MiB,"准备期间预留已被非计划使用");
  auto before=recent(d); limit=Clock::now()+std::chrono::seconds(40);
  while((block(before,sources[0]).flags&6)!=6||(block(before,sources[1]).flags&6)!=6) {
    require(Clock::now()<limit,"真实读窗口预热超时");
    std::this_thread::sleep_for(std::chrono::milliseconds(100)); before=recent(d);
  }
  logger->Flush(); unsigned previous=gc_checks(out+"/zenfs.run.log");
  limit=Clock::now()+std::chrono::seconds(15);
  do {
    require(Clock::now()<limit,"未看到原十秒GC节拍");
    std::this_thread::sleep_for(std::chrono::milliseconds(100)); logger->Flush();
  } while(gc_checks(out+"/zenfs.run.log")==previous);
  // 仅一次观察节拍对齐：五秒后预读，再固定持续读；不调用或唤醒维护。
  std::this_thread::sleep_for(std::chrono::seconds(5));
  before=recent(d);
  require(block(before,sources[0]).rc==0&&block(before,sources[1]).rc==0,"候选已有非计划读取");
  std::ofstream observations(out+"/recent.csv");
  observations<<"阶段,组号,设备会话高位,设备会话低位,Zone,设备代际,块编号,快照纳秒,代际起点纳秒,RC,十秒真实增量,三十秒真实增量,窗口标志\n";
  record(observations,"热读前",before,sources);
  fixed_reads(fs,file(1),2,3904,out+"/hot.reads.csv");
  auto ready=recent(d); record(observations,"释放前",ready,sources);
  auto hot=block(ready,sources[1]);
  require(hot.rc==3904&&hot.delta10==3904&&hot.delta30==3904&&hot.flags==7&&
          hot.block_id==block(before,sources[1]).block_id&&hot.generation==block(before,sources[1]).generation&&
          block(ready,sources[0]).rc==0,"候选真实同块读状态不符");
  std::ofstream states(out+"/states.csv");
  states<<"阶段,经过纳秒,预留存在,预留Zone,预留尾部,预留有效量,预留写指针,预留主机代际,空闲字节,有效字节,满区可回收字节,种子Zone,种子片段,种子长度,冷Zone,冷片段,冷长度,热Zone,热片段,热长度\n";
  Info(logger,"独立尾部释放：冷Zone=%lu，热Zone=%lu，预留Zone=%lu，尾部=16777216，热RC=3904，固定观察35秒，后续业务读1120次，速率32次每秒，无业务写入",sources[0],sources[1],r.zone);
  for(auto& guard:guards) { check(guard->Close(IOOptions(),nullptr)); guard.reset(); }
  // 准备句柄也持写锁；先释放，再记录实际可见映射，不能把缺项当丢失。
  save_state(fs,d,states,"释放后起点",0);
  std::unique_ptr<FSRandomAccessFile> reader;
  check(fs.NewRandomAccessFile(file(1),o,&reader,nullptr));
  Buffer expected,scratch; expected.fill(2);
  std::ofstream reads(out+"/ongoing.reads.csv");
  require(reads.good(),"持续读取记录创建失败");
  reads<<"序号,计划纳秒,开始纳秒,API耗时纳秒,字节\n";
  auto origin=Clock::now(); bool first=false;
  // 固定1120次读取，不根据维护结果停止、追加载荷或调整速率。
  for(uint64_t i=0;i<1120;++i) {
    const uint64_t due=i*1000000000ULL/32;
    std::this_thread::sleep_until(origin+std::chrono::nanoseconds(due));
    Slice result; auto begin=Clock::now();
    check(reader->Read(0,4096,IOOptions(),&result,scratch.get(),nullptr));
    auto end=Clock::now();
    require(result.size()==4096&&std::memcmp(result.data(),expected.get(),4096)==0,"持续读取数据不符");
    reads<<i<<','<<due<<','<<std::chrono::duration_cast<std::chrono::nanoseconds>(begin-origin).count()<<','
         <<std::chrono::duration_cast<std::chrono::nanoseconds>(end-begin).count()<<",4096\n";
    if(i%16==0) record(observations,"持续观察",recent(d),sources);
    auto cold=find(fs,file(0)),warm=find(fs,file(1));
    if(!first&&cold&&warm&&(cold->zone_start!=sources[0]||warm->zone_start!=sources[1])) {
      auto current=reserve_observation(d);
      if(!current.known) continue;
      save_state(fs,d,states,"首次迁移后",std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-origin).count());
      first=true; std::cout<<"已观察首次维护，继续固定35秒窗口"<<std::endl;
    }
  }
  std::this_thread::sleep_until(origin+std::chrono::seconds(35));
  reads.close(); reader.reset(); require(reads.good(),"持续读取记录写入失败");
  require(Clock::now()-origin<std::chrono::seconds(36),"固定35秒输入超时，不延长窗口重跑");
  require(first,"固定窗口未观察到候选迁移");
  save_state(fs,d,states,"固定窗口结束",std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-origin).count());
  record(observations,"观察结束",recent(d),sources);
  Info(logger,"独立尾部结束：固定35秒与1120次持续读完成，无追加业务写入或删除，结论由离线日志核验");
  std::cout<<"固定功能窗口完成；不能据此宣称RR或净迁移收益"<<std::endl;
}
static void tail_verify(ZenFS& fs,const std::string& out) {
  verify(fs,out);
  FileOptions o; o.use_direct_reads=true; Buffer expected,scratch; expected.fill(50);
  std::unique_ptr<FSRandomAccessFile> reader; check(fs.NewRandomAccessFile(Seed,o,&reader,nullptr));
  uint64_t size=0; check(fs.GetFileSize(Seed,IOOptions(),&size,nullptr)); require(size==240*MiB,"种子长度不符");
  for(uint64_t off=0;off<size;off+=MiB) {
    Slice result; check(reader->Read(off,MiB,IOOptions(),&result,scratch.get(),nullptr));
    require(result.size()==MiB&&std::memcmp(result.data(),expected.get(),MiB)==0,"种子完整字节比对失败");
  }
  require(fs.FileExists(SeedDead,IOOptions(),nullptr).IsNotFound(),"已退役种子填充仍存在");
  std::ofstream summary(out+"/tail.verify.csv");
  summary<<"完整有效数据字节,填充文件数,每填充文件抽检首尾字节\n"<<288*MiB<<",24,4096\n";
  summary.close(); require(summary.good(),"完整校验记录失败");
  std::cout<<"独立完整校验通过：288MiB有效数据，24个填充文件各首尾4KiB；未启动维护线程"<<std::endl;
}
int main(int argc,char** argv) {
  try {
    require(argc==4,"用法：程序 run|verify greedy|guarded 本轮独立结果目录");
    std::string mode=argv[1],strategy=argv[2],out=argv[3]; bool readonly=mode=="verify";
    require((mode=="run"||readonly)&&(strategy=="greedy"||strategy=="guarded"),"非法阶段或策略");
    require(out=="/home/femu/rrzc-tail-pair-results/guarded-tail-"+strategy+"-20261014"&&
            std::filesystem::is_directory(out),"不是本轮独立目录");
    const rlimit limit={64*MiB,64*MiB}; require(setrlimit(RLIMIT_FSIZE,&limit)==0,"文件上限设置失败");
    require(getenv("ZENFS_RR_ENABLE")&&std::string(getenv("ZENFS_RR_ENABLE"))==(readonly?"0":"1"),"RR开关不符");
    require(!getenv("ZENFS_ZC_TEST_ENABLE")&&!getenv("ZENFS_ZC_TEST_SOURCE")&&!getenv("ZENFS_RRZC_MODE")&&
            !getenv("ZENFS_RRZC_V1_GROWTH")&&!getenv("ZENFS_RRZC_SHORT_SHADOW")&&!getenv("ZENFS_RRZC_COST_OBSERVE")&&
            !getenv("ZENFS_RRZC_V1_SHADOW")&&!getenv("ZENFS_RRZC_V1_ENABLE"),"禁止人工选区或旧机制混用");
    if(!readonly) {
      require(getenv("ZENFS_RRZC_SIMPLE_MODE")&&std::string(getenv("ZENFS_RRZC_SIMPLE_MODE"))=="valid"&&
              getenv("ZENFS_RRZC_RECLAIM_BYTES")&&std::string(getenv("ZENFS_RRZC_RECLAIM_BYTES"))=="201326592","原Greedy或预算不符");
      const char* flag=getenv("ZENFS_RRZC_GUARDED_MODE");
      require(flag&&std::string(flag)==(strategy=="guarded"?"execute":"shadow"),"独立约束入口不符");
    } else require(!getenv("ZENFS_RRZC_GUARDED_MODE")&&!getenv("ZENFS_RRZC_SIMPLE_MODE"),"校验禁止维护入口");
    std::string logfile=out+"/zenfs."+mode+".log"; require(!std::filesystem::exists(logfile),"拒绝覆盖日志");
    std::shared_ptr<Logger> logger; check(Env::Default()->NewLogger(logfile,&logger)); logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger); check(owned->Open(readonly,true));
    auto* d=owned.get(); auto fs=std::make_unique<ZenFS>(owned.release(),FileSystem::Default(),logger); check(fs->Mount(readonly));
    if(readonly) tail_verify(*fs,out); else tail_run(*fs,*d,logger,out);
    return 0;
  } catch(const std::exception& e) { std::cerr<<"尾部小配对失败："<<e.what()<<std::endl; return 1; }
}
