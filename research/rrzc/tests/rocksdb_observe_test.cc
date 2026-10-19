// 真实RocksDB生命周期观察：只使用公开接口，不按未来寿命修改选区。
// 先在普通文件系统做小回归，再在新空ZNS设备观察；不直接调用GC/RR/Reset。
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include "rocksdb/db.h"
#include "rocksdb/env.h"
#include "rocksdb/listener.h"
#include "rocksdb/metadata.h"
#include "rocksdb/table.h"
#include "rocksdb/write_batch.h"
#include "logging/logging.h"
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "plugin/zenfs/fs/snapshot.h"
#include "rocksdb_observe_plan.h"
using namespace rocksdb;
using Clock=std::chrono::steady_clock;
constexpr uint64_t MiB=1ULL<<20;
constexpr unsigned HotKeys=262144,Batch=64,Seconds=120,ReadRate=400;
static void require(bool value,const std::string& message) {if(!value) throw std::runtime_error(message);}
template<class S> static void check(const S& status) {require(status.ok(),status.ToString());}
static uint64_t ns(Clock::duration value) {return std::chrono::duration_cast<std::chrono::nanoseconds>(value).count();}
static std::string key(unsigned k) {std::string s=std::to_string(k);return "k"+std::string(8-s.size(),'0')+s;}
static std::string value(unsigned k,uint64_t version) {
  std::string s(4096,char((k+version)%251));uint64_t id=k;
  std::memcpy(s.data(),&id,8);std::memcpy(s.data()+8,&version,8);return s;
}
static uint64_t final_version(unsigned k,unsigned hot,uint64_t writes) {
  return k<hot&&k<writes?writes-1-(writes-1-k)%hot+1:0;
}
struct Trace {
  Clock::time_point origin=Clock::now();
  std::atomic<unsigned> phase{0};std::mutex mutex;std::ofstream events;uint64_t serial=0;
  explicit Trace(const std::string& out):events(out+"/events.csv") {
    require(events.good(),"事件日志创建失败");
    events<<"序号,经过纳秒,阶段,事件,文件,任务,成功,字节,层级\n";
  }
  uint64_t now() const {return ns(Clock::now()-origin);}
  void event(const std::string& type,const std::string& path,int job,bool ok,uint64_t bytes=0,int level=-1) {
    std::lock_guard<std::mutex> lock(mutex);
    events<<serial++<<','<<now()<<','<<phase<<','<<type<<','<<path<<','<<job<<','<<ok<<','<<bytes<<','<<level<<'\n';
    events.flush();
  }
};
class Listener:public EventListener {
  std::shared_ptr<Trace> trace;
 public:
  explicit Listener(std::shared_ptr<Trace> t):trace(std::move(t)) {}
  void OnTableFileCreated(const TableFileCreationInfo& i) override {trace->event("SST创建",i.file_path,i.job_id,i.status.ok(),i.file_size);}
  void OnTableFileDeleted(const TableFileDeletionInfo& i) override {trace->event("SST删除",i.file_path,i.job_id,i.status.ok());}
  void OnFlushCompleted(DB*,const FlushJobInfo& i) override {trace->event("Flush完成",i.file_path,i.job_id,true,0,0);}
  void OnCompactionBegin(DB*,const CompactionJobInfo& i) override {
    trace->event("Compaction开始","",i.job_id,true,0,i.output_level);
    for(const auto& f:i.input_files) trace->event("Compaction输入",f,i.job_id,true,0,i.base_input_level);
  }
  void OnCompactionCompleted(DB*,const CompactionJobInfo& i) override {
    trace->event("Compaction完成","",i.job_id,i.status.ok(),0,i.output_level);
    for(const auto& f:i.output_files) trace->event("Compaction输出",f,i.job_id,i.status.ok(),0,i.output_level);
  }
  void OnBackgroundError(BackgroundErrorReason reason,Status* error) override {
    trace->event("后台错误","",static_cast<int>(reason),error->ok());
  }
};
static Options options(Env* env,std::shared_ptr<Trace> trace,bool smoke,bool readonly) {
  Options o;o.env=env;o.create_if_missing=!readonly;o.error_if_exists=!readonly;
  o.compression=kNoCompression;o.write_buffer_size=smoke?65536:16*MiB;
  o.target_file_size_base=smoke?65536:16*MiB;o.max_bytes_for_level_base=smoke?MiB:128*MiB;
  o.max_compaction_bytes=smoke?4*MiB:128*MiB;o.level0_file_num_compaction_trigger=2;
  o.max_background_jobs=2;o.max_write_buffer_number=4;o.disable_auto_compactions=readonly;
  o.use_direct_reads=!smoke;o.use_direct_io_for_flush_and_compaction=!smoke;
  BlockBasedTableOptions table;table.no_block_cache=true;o.table_factory.reset(NewBlockBasedTableFactory(table));
  if(trace) o.listeners.push_back(std::make_shared<Listener>(std::move(trace)));
  return o;
}
static void drain(DB& db) {
  FlushOptions flush;flush.wait=true;check(db.Flush(flush));
  WaitForCompactOptions wait;wait.wait_for_purge=true;wait.timeout=std::chrono::seconds(60);
  check(db.WaitForCompact(wait));
}
static void verify(DB& db,unsigned keys,unsigned hot,uint64_t writes,const rrzc_real::Plan* plan=nullptr) {
  ReadOptions read;read.fill_cache=false;read.verify_checksums=true;
  std::unique_ptr<Iterator> it(db.NewIterator(read));unsigned count=0;
  for(it->SeekToFirst();it->Valid();it->Next(),++count) {
    require(count<keys&&it->key().ToString()==key(count),"独立校验键集合不符");
    const unsigned ordinal=plan&&plan->anchors?rrzc_anchor::Ordinal(count):count;
    const unsigned domain=plan&&plan->anchors?rrzc_anchor::WritableKeys:hot;
    require(it->value().ToString()==value(count,final_version(ordinal,domain,writes)),"独立校验最新值不符");
  }
  check(it->status());require(count==keys,"独立校验键数不符");
  std::cout<<"最新值全量校验通过：键数="<<count<<"，业务字节="<<uint64_t(count)*4096<<std::endl;
}
class Observer {
  DB& db;ZenFS& fs;ZonedBlockDevice& device;std::shared_ptr<Trace> trace;
  std::ofstream zones,files,live;uint64_t serial=0;
 public:
  Observer(DB& d,ZenFS& f,ZonedBlockDevice& z,std::shared_ptr<Trace> t,const std::string& out)
    :db(d),fs(f),device(z),trace(std::move(t)),zones(out+"/zones.csv"),files(out+"/files.csv"),live(out+"/live.csv") {
    zones<<"批次,阶段,查询开始纳秒,查询结束纳秒,Zone,有效字节,写指针,剩余字节,容量字节,会话高位,会话低位,设备代际前,设备代际后,主机代际前,主机代际后,主机序号前,主机序号后,统计稳定,块编号,RC,十秒增量,三十秒增量,窗口标志,设备快照纳秒,映射覆盖字节\n";
    files<<"批次,阶段,Zone,文件编号,文件,片段起点,区内有效字节\n";
    live<<"批次,查询开始纳秒,查询结束纳秒,文件,文件号,层级,字节,正在Compaction,最小序列,最大序列,创建墙钟秒\n";
  }
  void sample() {
    uint64_t begin=trace->now();unsigned phase=trace->phase;
    rrzc_v1::RecentReport a,b;check(device.GetZoneRecentReads(&a));
    auto before=device.GetZoneResetStates();
    ZenFSSnapshot snapshot;ZenFSSnapshotOptions want;want.zbd_=want.zone_=want.zone_file_=true;
    fs.GetZenFSSnapshot(snapshot,want);
    std::vector<LiveFileMetaData> metadata;db.GetLiveFilesMetaData(&metadata);
    check(device.GetZoneRecentReads(&b));auto after=device.GetZoneResetStates();uint64_t end=trace->now();
    require(a.threshold==4096&&b.threshold==4096&&a.zones.size()==32&&b.zones.size()==32,"观察设备几何或阈值不符");
    std::map<uint64_t,uint64_t> covered;
    for(const auto& f:snapshot.zone_files_) for(const auto& e:f.extents) {
      covered[e.zone_start]+=e.length;
      files<<serial<<','<<phase<<','<<e.zone_start<<','<<f.file_id<<','<<f.filename<<','<<e.start<<','<<e.length<<'\n';
    }
    for(const auto& z:snapshot.zones_) {
      size_t index=z.start/(256*MiB);const auto& x=a.zones.at(index);const auto& y=b.zones.at(index);
      const auto& h=before.at(z.start);const auto& j=after.at(z.start);
      bool stable=a.session_hi==b.session_hi&&a.session_lo==b.session_lo&&x.generation==y.generation&&
          h.generation==j.generation&&h.sequence==j.sequence&&h.known&&j.known&&h.stable&&j.stable&&end-begin<=2000000000ULL;
      zones<<serial<<','<<phase<<','<<begin<<','<<end<<','<<z.start<<','<<z.used_capacity<<','<<z.wp<<','<<z.capacity<<','<<z.max_capacity<<','
        <<b.session_hi<<','<<b.session_lo<<','<<x.generation<<','<<y.generation<<','<<h.generation<<','<<j.generation<<','<<h.sequence<<','<<j.sequence<<','<<stable<<','
        <<y.block_id<<','<<y.rc<<','<<y.delta10<<','<<y.delta30<<','<<y.flags<<','<<y.snapshot_ns<<','<<covered[z.start]<<'\n';
    }
    for(const auto& m:metadata) live<<serial<<','<<begin<<','<<end<<','<<m.directory<<'/'<<m.relative_filename<<','<<m.file_number<<','<<m.level<<','<<m.size<<','<<m.being_compacted<<','<<m.smallest_seqno<<','<<m.largest_seqno<<','<<m.file_creation_time<<'\n';
    zones.flush();files.flush();live.flush();require(zones.good()&&files.good()&&live.good(),"观察记录写入失败");++serial;
  }
};
static void put_batch(DB& db,unsigned first,unsigned count,unsigned hot,uint64_t version,bool initial,const rrzc_real::Plan* plan=nullptr) {
  WriteBatch batch;
  for(unsigned i=0;i<count;++i) {
    unsigned k=initial?first+i:plan&&plan->anchors?rrzc_anchor::WriteKey(first+i):(first+i)%hot;
    check(batch.Put(key(k),value(k,initial?0:version+i)));
  }
  WriteOptions write;write.disableWAL=true;check(db.Write(write,&batch));
}
static void smoke(const std::string& out) {
  auto trace=std::make_shared<Trace>(out);auto o=options(Env::Default(),trace,true,false);
  DB* raw=nullptr;check(DB::Open(o,out+"/db",&raw));std::unique_ptr<DB> db(raw);
  for(unsigned i=0;i<1024;i+=Batch) put_batch(*db,i,Batch,512,0,true);
  drain(*db);trace->phase=1;
  for(unsigned i=0;i<2048;i+=Batch) put_batch(*db,i,Batch,512,i+1,false);
  drain(*db);check(db->Close());db.reset();
  o.create_if_missing=false;o.error_if_exists=false;o.disable_auto_compactions=true;
  check(DB::OpenForReadOnly(o,out+"/db",&raw));db.reset(raw);verify(*db,1024,512,2048);check(db->Close());
  std::cout<<"普通文件系统真实RocksDB最小回归通过，未打开NVMe。"<<std::endl;
}
static void anchor_smoke(const std::string& out,const rrzc_real::Plan& plan) {
  auto o=options(Env::Default(),{},true,false);DB* raw=nullptr;
  check(DB::Open(o,out+"/anchor-db",&raw));std::unique_ptr<DB> db(raw);
  std::map<unsigned,uint64_t> expected;std::vector<unsigned> ordinals;
  for(unsigned stripe=0;stripe<rrzc_anchor::Stripes;++stripe) {
    for(unsigned offset:{rrzc_anchor::AnchorOffset-1,rrzc_anchor::AnchorOffset,rrzc_anchor::AnchorOffset+rrzc_anchor::AnchorKeys/2,
                         rrzc_anchor::AnchorOffset+rrzc_anchor::AnchorKeys-1,rrzc_anchor::AnchorOffset+rrzc_anchor::AnchorKeys})
      expected[stripe*rrzc_anchor::StripeKeys+offset]=0;
    for(unsigned offset:{rrzc_anchor::AnchorOffset-1,rrzc_anchor::AnchorOffset})
      ordinals.push_back(stripe*rrzc_anchor::WritablePerStripe+offset);
  }
  WriteBatch initial;WriteOptions write;write.disableWAL=true;
  for(const auto& e:expected) check(initial.Put(key(e.first),value(e.first,0)));
  check(db->Write(write,&initial));drain(*db);
  for(size_t i=0;i<ordinals.size();++i) {
    put_batch(*db,ordinals[i],1,HotKeys,i+1,false,&plan);
    expected[rrzc_anchor::WriteKey(ordinals[i])]=i+1;
  }
  drain(*db);check(db->Close());db.reset();
  o.create_if_missing=false;o.error_if_exists=false;check(DB::OpenForReadOnly(o,out+"/anchor-db",&raw));db.reset(raw);
  for(const auto& e:expected) {std::string got;check(db->Get(ReadOptions{},key(e.first),&got));require(got==value(e.first,e.second),"锚点或跨空洞更新内容错误");}
  check(db->Close());std::cout<<"固定条带锚点普通文件系统跨边界回归通过，未打开NVMe。"<<std::endl;
}
int main(int argc,char** argv) {
  try {
    require(argc==3,"用法：程序 selftest|run|verify 新结果目录");
    rrzc_real::Plan plan;
    require(rrzc_real::Parse(getenv("RRZC_TEST_DB_PROFILE"),&plan),"只允许已登记的固定测试计划");
    const unsigned Keys=plan.keys;
    std::string mode=argv[1],out=argv[2];require(std::filesystem::is_directory(out),"结果目录不存在");
    if(mode=="selftest") {smoke(out);if(plan.anchors) anchor_smoke(out,plan);return 0;}
    require(mode=="run"||mode=="verify","模式不符");bool readonly=mode=="verify";
    require(out==std::string("/home/femu/rrzc-stream-results/")+plan.label,"不是本轮隔离目录");
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(out+"/zenfs."+mode+".log",&logger));logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);check(owned->Open(readonly,true));auto* device=owned.get();
    auto fs=std::make_shared<ZenFS>(owned.release(),FileSystem::Default(),logger);check(fs->Mount(readonly));
    std::shared_ptr<FileSystem> filesystem=fs;auto env=NewCompositeEnv(filesystem);
    auto trace=readonly?std::shared_ptr<Trace>{}:std::make_shared<Trace>(out);
    auto o=options(env.get(),trace,false,readonly);DB* raw=nullptr;
    if(readonly) check(DB::OpenForReadOnly(o,"/rrzc-real",&raw));else check(DB::Open(o,"/rrzc-real",&raw));
    std::unique_ptr<DB> db(raw);
    if(readonly) {verify(*db,Keys,HotKeys,uint64_t(plan.write_seconds)*plan.write_rate,&plan);check(db->Close());return 0;}
    Observer observer(*db,*fs,*device,trace,out);
    std::atomic<bool> stop{false};std::exception_ptr error;std::mutex error_mutex;
    auto guarded=[&](auto action) {try {action();} catch(...) {std::lock_guard<std::mutex> lock(error_mutex);if(!error) error=std::current_exception();stop=true;}};
    std::thread monitor([&]{guarded([&]{while(!stop) {auto due=Clock::now()+std::chrono::seconds(1);observer.sample();while(!stop&&Clock::now()<due) std::this_thread::sleep_for(std::chrono::milliseconds(20));}});});
    guarded([&]{
      for(unsigned i=0;i<Keys&&!stop;i+=Batch) put_batch(*db,i,Batch,HotKeys,0,true);
      require(!stop,"准备阶段观察失败");drain(*db);trace->phase=1;
      auto start=Clock::now();trace->event("混合业务开始","",-1,true);
      std::ofstream reads(out+"/reads.csv"),writes(out+"/writes.csv");
      reads<<"序号,计划纳秒,开始纳秒,延迟纳秒,键号,版本\n";
      writes<<"序号,计划纳秒,开始纳秒,延迟纳秒,起始键序号,键数,起始版本\n";
      std::thread writer([&]{guarded([&]{
        for(unsigned i=0;i<plan.write_seconds*plan.write_rate&&!stop;i+=Batch) {
          auto due=start+std::chrono::nanoseconds(uint64_t(i)*1000000000ULL/plan.write_rate);std::this_thread::sleep_until(due);
          auto begin=Clock::now();put_batch(*db,i,Batch,HotKeys,i+1,false,&plan);auto end=Clock::now();
          writes<<i/Batch<<','<<ns(due-trace->origin)<<','<<ns(begin-trace->origin)<<','<<ns(end-begin)<<','<<i<<','<<Batch<<','<<i+1<<'\n';
        }
      });});
      std::thread reader([&]{guarded([&]{ReadOptions ro;ro.fill_cache=false;ro.verify_checksums=true;
        for(unsigned i=0;i<Seconds*ReadRate&&!stop;++i) {
          auto due=start+std::chrono::nanoseconds(uint64_t(i)*1000000000ULL/ReadRate);std::this_thread::sleep_until(due);
          unsigned k=plan.anchors?rrzc_anchor::ReadKey(i):i%2?rrzc_real::ColdKey:((i/2)%16)*1024;
          std::string result;auto begin=Clock::now();check(db->Get(ro,key(k),&result));auto end=Clock::now();
          require(result.size()==4096,"业务读取长度不符");uint64_t version;std::memcpy(&version,result.data()+8,8);
          require(result==value(k,version),"业务读取内容损坏");
          reads<<i<<','<<ns(due-trace->origin)<<','<<ns(begin-trace->origin)<<','<<ns(end-begin)<<','<<k<<','<<version<<'\n';
        }
      });});
      writer.join();reader.join();require(!stop,"混合业务线程失败");
      reads.flush();writes.flush();require(reads.good()&&writes.good(),"业务日志写入失败");
      trace->phase=2;trace->event("混合业务结束","",-1,true);drain(*db);
    });
    stop=true;monitor.join();if(error) std::rethrow_exception(error);
    observer.sample();check(db->Close());db.reset();trace->phase=3;
    std::cout<<"真实RocksDB观察完成：固定混合业务秒数="<<std::max(Seconds,plan.write_seconds)<<"，初始键数="<<Keys<<"，写键数="<<plan.write_seconds*plan.write_rate<<"，读次数="<<Seconds*ReadRate<<std::endl;
    return 0;
  } catch(const std::exception& e) {std::cerr<<"真实RocksDB观察失败："<<e.what()<<std::endl;return 1;}
}
