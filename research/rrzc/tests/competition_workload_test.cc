// 独立三候选竞争负载，由成本负载派生；两个旧源和旧结果保持不变。
// 不是真实RocksDB SST，不直接调用GC/RR/Reset。
// 读热点和更新旧版本最初同区；新版本写到新区，提交后使源区旧版本失效。
// 读、写、采样并发；固定请求计划不根据选区结果自适应。
#include <algorithm>
#include <atomic>
#include <array>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <tuple>
#include <vector>
#include "competition_workload_plan.h"
#include "logging/logging.h"
#include "plugin/zenfs/fs/fs_zenfs.h"
#include "plugin/zenfs/fs/snapshot.h"
using namespace rocksdb;
using Clock = std::chrono::steady_clock;
constexpr uint64_t kMiB = 1ULL << 20;
constexpr unsigned kFillers = 18;
static void require(bool ok,const std::string& message) { if(!ok) throw std::runtime_error(message); }
template <typename S> static void check(const S& s) { require(s.ok(),s.ToString()); }
static uint64_t nanos(Clock::duration d) { return std::chrono::duration_cast<std::chrono::nanoseconds>(d).count(); }
static std::string anchor(unsigned g,unsigned kind) {
  return "/rrzc-stream/g"+std::to_string(g)+"."+rrzc_workload::Kind(kind)+".anchor.sst";
}
static std::string mutable_file(unsigned g,unsigned kind,unsigned slot) {
  return "/rrzc-stream/g"+std::to_string(g)+"."+rrzc_workload::Kind(kind)+".version"+std::to_string(slot)+".bin";
}
static unsigned seed_for(unsigned g,unsigned kind) { return 17+g*3+kind; }
static unsigned char pattern(uint64_t offset,unsigned seed) { return ((offset%kMiB)*131+17+seed*31)%251; }
struct Buffer {
  std::unique_ptr<char,decltype(&std::free)> data{nullptr,&std::free};
  Buffer() { void* p=nullptr; require(posix_memalign(&p,4096,kMiB)==0,"对齐缓冲申请失败"); data.reset(static_cast<char*>(p)); }
  char* get() { return data.get(); }
};
struct Request { uint64_t due_ns; unsigned generation,kind,phase,index; };
static std::vector<Request> make_reads(unsigned duration,bool victim_cases=false) {
  require(!victim_cases,"竞争场景不混用旧退役计划");
  std::vector<Request> result;
  for(unsigned g=0;g<duration/30;++g) {
    const uint64_t birth=uint64_t(g)*30*1000000000ULL;
    auto add=[&](unsigned kind,unsigned start,unsigned count,unsigned rate,unsigned phase) {
      for(unsigned i=0;i<count;++i) {
        uint64_t due=birth+uint64_t(start)*1000000000ULL+uint64_t(i)*1000000000ULL/rate;
        if(due>=uint64_t(duration)*1000000000ULL) break;
        result.push_back({due,g,kind,phase,i});
      }
    };
    for(unsigned kind=0;kind<3;++kind) {
      add(kind,10,3584,512,0); add(kind,17,41*4,4,1);
      // 两个热文件同时持续读24秒：较贵者32次/秒，较便宜者28次/秒。
      add(kind,58,24*rrzc_workload::PostRate(kind),rrzc_workload::PostRate(kind),2);
      add(kind,82,38*4,4,1);
    }
  }
  std::sort(result.begin(),result.end(),[](const Request& a,const Request& b) {
    return std::tie(a.due_ns,a.generation,a.kind)<std::tie(b.due_ns,b.generation,b.kind);
  });
  return result;
}
class Stream {
 public:
  ZenFS& fs; ZonedBlockDevice& device; std::shared_ptr<Logger> logger;
  FileOptions options;
  std::ofstream operations,states,pressures,manifest;
  // 仅测试观察：默认不采样，不参与任何RR或ZC决策。
  std::ofstream recent;
  unsigned recent_interval_ms=0;
  std::atomic<bool> recent_stop{false};
  Clock::time_point origin;
  std::mutex record_mutex,ready_mutex,error_mutex;
  std::condition_variable ready_cv;
  std::vector<bool> ready,read_done;
  std::vector<std::array<bool,3>> kind_read_done;
  bool victim_cases=false;
  std::atomic<bool> cancelled{false};
  std::exception_ptr error;
  unsigned duration;
  uint64_t serials[2]={0,0};
  Stream(ZenFS& f,ZonedBlockDevice& d,std::shared_ptr<Logger> l,unsigned seconds,const std::string& out)
      : fs(f),device(d),logger(l),operations(out+"/operations.csv"),states(out+"/states.csv"),
        pressures(out+"/pressure.csv"),manifest(out+"/manifest.csv"),origin(Clock::now()),
        ready(seconds/30,false),read_done(seconds/30,false),kind_read_done(seconds/30,{false,false,false}),duration(seconds) {
    options.use_direct_reads=options.use_direct_writes=true;
    require(operations.good()&&states.good()&&pressures.good()&&manifest.good(),"记录文件创建失败");
    operations<<"线程,序号,阶段,操作,代号,文件,偏移,字节,开始纳秒,延迟纳秒,排程迟到纳秒,成功\n";
    states<<"经过纳秒,空闲字节,有效字节,满区可回收字节,空区,部分区,满区,累计IO区成功重置\n";
    pressures<<"经过纳秒,Zone,最大块读数,总读数,达阈值块,主机代际\n";
    manifest<<"文件,有效字节,模式种子,是否填充\n";
    const char* cases=getenv("RRZC_TEST_VICTIM_PROFILE");
    if(cases) {
      require(std::string(cases)=="maintenance-cases"&&seconds==180,"受控维护场景仅支持本轮180秒");
      victim_cases=true;
    }
    const char* interval=getenv("RRZC_TEST_RECENT_SAMPLE_MS");
    if(interval) {
      require(std::string(interval)=="500"||std::string(interval)=="1000","逐块观察仅接受500或1000毫秒");
      recent_interval_ms=std::stoul(interval);
      require(!std::filesystem::exists(out+"/recent.csv"),"拒绝覆盖逐块轨迹");
      recent.open(out+"/recent.csv"); require(recent.good(),"逐块轨迹创建失败");
      recent<<"批次,查询开始纳秒,查询结束纳秒,设备会话高位,设备会话低位,Zone,区索引,设备代际,块编号,通道,LUN,plane,block,设备快照纳秒,代际起点纳秒,RC,十秒真实增量,三十秒真实增量,窗口标志,RR阈值\n";
    }
  }
  template <typename A> void call(unsigned thread,const std::string& phase,const std::string& op,
                                 int g,const std::string& file,uint64_t offset,uint64_t bytes,A action,
                                 Clock::time_point due=Clock::time_point{}) {
    auto begin=Clock::now(); auto status=action(); auto end=Clock::now();
    uint64_t late=due==Clock::time_point{}||begin<=due?0:nanos(begin-due);
    std::lock_guard<std::mutex> lock(record_mutex);
    operations<<thread<<','<<serials[thread]++<<','<<phase<<','<<op<<','<<g<<','<<file<<','<<offset<<','<<bytes<<','
              <<nanos(begin-origin)<<','<<nanos(end-begin)<<','<<late<<','<<status.ok()<<'\n';
    check(status);
  }
  bool pause(Clock::time_point due) {
    // 小段等待便于失败时退出，不改变正常请求计划。
    while(!cancelled&&Clock::now()<due) std::this_thread::sleep_for(std::min(due-Clock::now(),Clock::duration(std::chrono::milliseconds(20))));
    return !cancelled;
  }
  template <typename A> void guarded(A action) {
    try { action(); } catch(...) {
      { std::lock_guard<std::mutex> lock(error_mutex); if(!error) error=std::current_exception(); }
      cancelled=true; ready_cv.notify_all();
    }
  }
  void write(const std::string& file,uint64_t bytes,unsigned seed,Env::WriteLifeTimeHint hint,
             int g,Buffer& buffer,Clock::time_point pace,const std::function<void()>& before_close={}) {
    require(fs.FileExists(file,IOOptions(),nullptr).IsNotFound(),"拒绝覆盖业务文件");
    for(uint64_t i=0;i<kMiB;++i) buffer.get()[i]=pattern(i,seed);
    std::unique_ptr<FSWritableFile> writer;
    call(0,"更新写","创建",g,file,0,0,[&]{return fs.NewWritableFile(file,options,&writer,nullptr);});
    writer->SetWriteLifeTimeHint(hint);
    for(uint64_t off=0;off<bytes;off+=kMiB) {
      // 三个区每30秒写768MiB，约25.6MiB/s；不与旧双区负载直接比较性能。
      auto due=pace==Clock::time_point{}?Clock::time_point{}:pace+std::chrono::nanoseconds(off/kMiB*rrzc_workload::PaceNs);
      if(due!=Clock::time_point{}) require(pause(due),"写线程因其他线程失败停止");
      call(0,"更新写","写1MiB",g,file,off,kMiB,[&]{return writer->Append(Slice(buffer.get(),kMiB),IOOptions(),nullptr);},due);
    }
    if(before_close) before_close();
    call(0,"更新写","关闭",g,file,bytes,0,[&]{return writer->Close(IOOptions(),nullptr);});
  }
  void remove(const std::string& file,int g,bool protect_anchor) {
    std::unique_ptr<FSWritableFile> guard;
    std::string retired=file;
    if(protect_anchor) {
      // 持写锁先改成非SST退役名，关闭时仍保留链接；随后才删除。
      // 避免旧ZenFS在已删除文件的Close日志中读取空linkfiles_[0]。
      // 不追加数据；非SST退役名在关闭后的短窗口也不进入RR迁移。
      retired=file+".retired.bin";
      require(fs.FileExists(retired,IOOptions(),nullptr).IsNotFound(),"退役名冲突");
      call(0,"读热点到期","退役保护",g,file,0,0,[&]{return fs.ReopenWritableFile(file,options,&guard,nullptr);});
      call(0,"读热点到期","退役改名",g,file,0,0,[&]{return fs.RenameFile(file,retired,IOOptions(),nullptr);});
      call(0,"读热点到期","关闭保护",g,retired,0,0,[&]{return guard->Close(IOOptions(),nullptr);});
      guard.reset();
    }
    call(0,"旧版本失效","删除",g,retired,0,0,[&]{return fs.DeleteFile(retired,IOOptions(),nullptr);});
  }
  std::unique_ptr<FSWritableFile> open_version(unsigned g,unsigned kind) {
    auto file=mutable_file(g,kind,0);
    require(fs.FileExists(file,IOOptions(),nullptr).IsNotFound(),"拒绝覆盖业务版本");
    std::unique_ptr<FSWritableFile> writer;
    call(0,"更新写","创建",g,file,0,0,[&]{return fs.NewWritableFile(file,options,&writer,nullptr);});
    writer->SetWriteLifeTimeHint(kind?Env::WLTH_LONG:Env::WLTH_MEDIUM);
    return writer;
  }
  void append_version(FSWritableFile& writer,unsigned g,unsigned kind,uint64_t begin_mib,
                      uint64_t count_mib,Buffer& buffer,Clock::time_point birth,uint64_t& written_mib) {
    // 首块的文件句柄和数据在锚点关闭前已准备好；其余数据在切换时准备。
    if(begin_mib) for(uint64_t i=0;i<kMiB;++i) buffer.get()[i]=pattern(i,100+g*31+kind*15);
    for(uint64_t i=0;i<count_mib;++i) {
      // 首块与锚点末块同一计划时刻，连续交接，不留一次节流空档。
      auto due=birth+std::chrono::nanoseconds((written_mib-(begin_mib==0&&i==0))*rrzc_workload::PaceNs);
      require(pause(due),"更新线程停止");
      call(0,"更新写","写1MiB",g,mutable_file(g,kind,0),(begin_mib+i)*kMiB,kMiB,
           [&]{return writer.Append(Slice(buffer.get(),kMiB),IOOptions(),nullptr);},due);
      ++written_mib;
    }
  }
  void check_layout(unsigned g) {
    ZenFSSnapshot snapshot; ZenFSSnapshotOptions opts; opts.zone_=opts.zone_file_=true;
    fs.GetZenFSSnapshot(snapshot,opts);
    for(unsigned kind=0;kind<3;++kind) {
      uint64_t zone=0,covered=0,count=0;
      for(const auto& e:snapshot.extents_) {
        bool match=e.filename==anchor(g,kind);
        match=match||e.filename==mutable_file(g,kind,0);
        if(match) { if(!count) zone=e.zone_start; require(zone==e.zone_start,"一代同类载荷跨Zone，负载条件不成立"); covered+=e.length; ++count; }
      }
      require(count==2&&covered==256*kMiB&&device.GetIOZone(zone)->IsFull(),"冷热同区布局未真实写满");
    }
  }
  void writer_loop() {
    Buffer buffer;
    for(unsigned g=0;g<duration/30;++g) {
      auto birth=origin+std::chrono::seconds(g*30);
      require(pause(birth),"写线程停止");
      if(g>=4) {
        std::unique_lock<std::mutex> lock(ready_mutex);
        ready_cv.wait(lock,[&]{return cancelled||read_done[g-4];});
        require(!cancelled,"读线程失败，停止热点退役"); lock.unlock();
        for(unsigned kind=0;kind<3;++kind) remove(anchor(g-4,kind),g-4,true);
      }
      uint64_t written_mib=0;
      std::unique_ptr<FSWritableFile> versions[3];
      for(unsigned kind=0;kind<3;++kind) {
        uint64_t bytes=rrzc_workload::AnchorMiB(g,kind)*kMiB;
        // 三个版本句柄保持各自区活跃，使后续锚点不能借用前一个部分区。
        write(anchor(g,kind),bytes,seed_for(g,kind),kind?Env::WLTH_EXTREME:Env::WLTH_LONG,g,buffer,
              birth+std::chrono::nanoseconds(written_mib*rrzc_workload::PaceNs),[&]{
                versions[kind]=open_version(g,kind);
                for(uint64_t i=0;i<kMiB;++i) buffer.get()[i]=pattern(i,100+g*31+kind*15);
              });
        written_mib+=bytes/kMiB;
        // 每区的版本文件先真实写入1MiB并保持打开，直到该区写满。
        // ZoneFile正常写句柄持有active_zone_，避免维护迁移借用正在填充的部分区。
        // 不调用Zone加锁接口，不干预维护选区，也不改变ZenFS分配器。
        append_version(*versions[kind],g,kind,0,1,buffer,birth,written_mib);
      }
      { std::lock_guard<std::mutex> lock(ready_mutex); ready[g]=true; }
      ready_cv.notify_all();
      for(unsigned kind=0;kind<3;++kind) {
        uint64_t bytes=rrzc_workload::VersionMiB(g,kind)*kMiB;
        append_version(*versions[kind],g,kind,1,bytes/kMiB-1,buffer,birth,written_mib);
        auto file=mutable_file(g,kind,0);
        call(0,"更新写","关闭",g,file,bytes,0,[&]{return versions[kind]->Close(IOOptions(),nullptr);});
      }
      require(written_mib==768,"代际业务写入量不符");
      check_layout(g);
      // 先写完同批新版本，提交后使上一代旧版本失效。
      if(g) for(unsigned kind=0;kind<3;++kind) remove(mutable_file(g-1,kind,0),g-1,false);
      Info(logger,"滚动更新提交：代号=%u，经过纳秒=%lu，业务写入=805306368，源旧版本失效=%d",g,nanos(Clock::now()-origin),g>0);
      std::cout<<"更新代完成 "<<g+1<<'/'<<duration/30<<std::endl;
    }
  }
  void reader_loop() {
    auto requests=make_reads(duration,victim_cases);
    std::vector<uint64_t> remaining(duration/30,0);
    std::vector<std::array<uint64_t,3>> remaining_kind(duration/30,{0,0,0});
    for(const auto& r:requests) { ++remaining[r.generation]; ++remaining_kind[r.generation][r.kind]; }
    struct Readers { std::unique_ptr<FSRandomAccessFile> file[3]; };
    std::map<unsigned,Readers> readers;
    Buffer scratch;
    for(const auto& r:requests) {
      auto due=origin+std::chrono::nanoseconds(r.due_ns);
      require(pause(due),"读线程停止");
      if(!readers.count(r.generation)) {
        std::unique_lock<std::mutex> lock(ready_mutex);
        ready_cv.wait(lock,[&]{return cancelled||ready[r.generation];});
        require(!cancelled,"写线程失败，停止读取未发布代"); lock.unlock();
        auto& opened=readers[r.generation];
        for(unsigned kind=0;kind<3;++kind) {
          auto file=anchor(r.generation,kind);
          call(1,"业务读","打开读",r.generation,file,0,0,[&]{return fs.NewRandomAccessFile(file,options,&opened.file[kind],nullptr);});
        }
      }
      uint64_t offset=r.kind?0:(r.index%8)*65536;
      Slice result;
      const char* phase=r.phase==0?"阈值以下热读":r.phase==2?"更新后热读":"持续背景读";
      call(1,phase,"读4KiB",r.generation,anchor(r.generation,r.kind),offset,4096,[&]{
        return readers.at(r.generation).file[r.kind]->Read(offset,4096,IOOptions(),&result,scratch.get(),nullptr);
      },due);
      require(result.size()==4096,"业务短读");
      for(unsigned i=0;i<4096;++i) require(static_cast<unsigned char>(result.data()[i])==pattern(offset+i,seed_for(r.generation,r.kind)),"业务数据字节不符");
      if(--remaining_kind[r.generation][r.kind]==0) {
        if(victim_cases&&r.kind==1&&r.generation%3!=0) {
          // 退役前必须释放实际读句柄，不能将删除最后链接误作有效量立即归零。
          readers.at(r.generation).file[r.kind].reset();
          Info(logger,"受控热读停止：代号=%u，经过纳秒=%lu，文件=%s，场景=%s，实际读句柄已释放",
               r.generation,nanos(Clock::now()-origin),anchor(r.generation,1).c_str(),
               r.generation%3==1?"停止但仍有效":"即将退役");
        }
        { std::lock_guard<std::mutex> lock(ready_mutex); kind_read_done[r.generation][r.kind]=true; }
        ready_cv.notify_all();
      }
      if(--remaining[r.generation]==0) {
        readers.erase(r.generation);
        { std::lock_guard<std::mutex> lock(ready_mutex); read_done[r.generation]=true; }
        ready_cv.notify_all();
      }
    }
    Info(logger,"滚动业务读完成：实际4KiB请求=%lu，经过纳秒=%lu",requests.size(),nanos(Clock::now()-origin));
  }
  void sample() {
    auto elapsed=nanos(Clock::now()-origin);
    ZenFSSnapshot snapshot; ZenFSSnapshotOptions opts; opts.zbd_=opts.zone_=true;
    fs.GetZenFSSnapshot(snapshot,opts);
    auto generations=device.GetZoneResetStates();
    unsigned empty=0,partial=0,full=0; uint64_t resets=0;
    for(const auto& z:snapshot.zones_) {
      if(z.wp==z.start) ++empty; else if(z.capacity==0) ++full; else ++partial;
      resets+=generations.at(z.start).generation;
    }
    ZoneReadPressureReport pressure; check(device.GetZoneReadPressure(&pressure));
    require(pressure.read_threshold==4096&&pressure.blocks_per_zone==64&&pressure.zones.size()==32,"设备几何或RR阈值不符");
    { std::lock_guard<std::mutex> lock(record_mutex);
      states<<elapsed<<','<<snapshot.zbd_.free_space<<','<<snapshot.zbd_.used_space<<','<<snapshot.zbd_.reclaimable_space<<','<<empty<<','<<partial<<','<<full<<','<<resets<<'\n';
      for(const auto& p:pressure.zones) if(p.start>=3*256*kMiB)
        pressures<<elapsed<<','<<p.start<<','<<p.max_block_reads<<','<<p.sum_block_reads<<','<<p.blocks_at_threshold<<','<<generations.at(p.start).generation<<'\n';
      operations.flush(); states.flush(); pressures.flush();
      require(operations.good()&&states.good()&&pressures.good(),"记录写入失败");
    }
    // 空间、C1h、代际分次读取，不是原子快照。
    Info(logger,"滚动负载时钟：经过纳秒=%lu",elapsed);
  }
  void recent_loop() {
    uint64_t serial=0;
    auto due=origin;
    while(!recent_stop&&!cancelled) {
      // 查询期间不持业务记录锁或文件锁；只采当前最大块，不拼接不同块读数。
      const uint64_t begin=nanos(Clock::now()-origin);
      rrzc_v1::RecentReport report; check(device.GetZoneRecentReads(&report));
      const uint64_t end=nanos(Clock::now()-origin);
      require(report.threshold==4096&&report.blocks_per_zone==64&&report.zones.size()==32,
              "逐块采样设备几何或阈值不符");
      for(const auto& b:report.zones)
        recent<<serial<<','<<begin<<','<<end<<','<<report.session_hi<<','<<report.session_lo<<','
              <<b.zone<<','<<b.zone_index<<','<<b.generation<<','<<b.block_id<<','<<b.ch<<','<<b.lun<<','
              <<b.plane<<','<<b.block<<','<<b.snapshot_ns<<','<<b.epoch_start_ns<<','<<b.rc<<','
              <<b.delta10<<','<<b.delta30<<','<<b.flags<<','<<report.threshold<<'\n';
      recent.flush(); require(recent.good(),"逐块轨迹写入失败");
      ++serial;
      due+=std::chrono::milliseconds(recent_interval_ms);
      // 迟到时不追赶连发采样，也不调整业务读写计划。
      if(due<Clock::now()) due=Clock::now()+std::chrono::milliseconds(recent_interval_ms);
      while(!recent_stop&&!cancelled&&Clock::now()<due)
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
  }
  void retire_case_loop() {
    // 预设时间只属于测试负载；不输入Greedy或V1，不直接调用清理/Reset。
    for(unsigned g=2;g<duration/30;g+=3) {
      auto due=origin+std::chrono::seconds(g*30+66);
      if(due>=origin+std::chrono::seconds(duration)) break;
      require(pause(due),"受控退役线程停止");
      std::unique_lock<std::mutex> lock(ready_mutex);
      ready_cv.wait(lock,[&]{return cancelled||kind_read_done[g][1];});
      require(!cancelled,"读线程失败，拒绝退役"); lock.unlock();
      remove(anchor(g,1),g,true);
      Info(logger,"受控文件退役完成：代号=%u，经过纳秒=%lu，文件=%s，预设年龄秒=66",
           g,nanos(Clock::now()-origin),anchor(g,1).c_str());
    }
  }
  void run() {
    require(device.GetUsedSpace()==0,"不是新空设备，拒绝混入旧数据");
    check(fs.CreateDir("/rrzc-stream",IOOptions(),nullptr));
    Buffer buffer;
    for(unsigned i=0;i<kFillers;++i) {
      write("/rrzc-stream/filler"+std::to_string(i)+".bin",256*kMiB,200+i,Env::WLTH_MEDIUM,-1,buffer,Clock::time_point{});
      if(i%4==3) std::cout<<"固定填充完成 "<<i+1<<'/'<<kFillers<<"区"<<std::endl;
    }
    origin=Clock::now(); sample();
    std::thread observer;
    if(recent_interval_ms) observer=std::thread([&]{guarded([&]{recent_loop();});});
    std::thread writer([&]{guarded([&]{writer_loop();});});
    std::thread reader([&]{guarded([&]{reader_loop();});});
    std::thread retire;
    if(victim_cases) retire=std::thread([&]{guarded([&]{retire_case_loop();});});
    guarded([&]{for(unsigned sec=5;sec<=duration;sec+=5) {if(!pause(origin+std::chrono::seconds(sec))) break; sample();}});
    writer.join(); reader.join();
    if(retire.joinable()) retire.join();
    recent_stop=true;
    if(observer.joinable()) observer.join();
    if(error) std::rethrow_exception(error);
    sample();
    unsigned last=duration/30-1;
    std::map<std::string,std::pair<uint64_t,unsigned>> expected;
    for(unsigned g=last>=3?last-3:0;g<=last;++g) for(unsigned kind=0;kind<3;++kind) {
      if(victim_cases&&kind==1&&g%3==2&&g*30+66<duration) continue;
      expected[anchor(g,kind)]={rrzc_workload::AnchorMiB(g,kind)*kMiB,seed_for(g,kind)};
    }
    for(unsigned kind=0;kind<3;++kind)
      expected[mutable_file(last,kind,0)]={rrzc_workload::VersionMiB(last,kind)*kMiB,100+last*31+kind*15};
    for(unsigned i=0;i<kFillers;++i) expected["/rrzc-stream/filler"+std::to_string(i)+".bin"]={256*kMiB,200+i};
    std::vector<std::string> children; check(fs.GetChildren("/rrzc-stream",IOOptions(),&children,nullptr));
    require(children.size()==expected.size(),"最终文件集合数量不符");
    for(const auto& child:children) require(expected.count("/rrzc-stream/"+child),"最终存在非计划文件");
    for(const auto& item:expected) {
      uint64_t size=0; check(fs.GetFileSize(item.first,IOOptions(),&size,nullptr)); require(size==item.second.first,"最终文件长度不符");
      manifest<<item.first<<','<<size<<','<<item.second.second<<','<<(item.first.find("/filler")!=std::string::npos)<<'\n';
    }
    manifest.flush(); require(manifest.good(),"最终清单写入失败");
    std::cout<<"滚动读写完成，固定请求计划已执行；数据校验由只读独立进程完成"<<std::endl;
  }
};
static void verify(ZenFS& fs,const std::string& out) {
  std::ifstream manifest(out+"/manifest.csv"); require(manifest.good(),"缺少最终数据清单");
  std::string line; std::getline(manifest,line);
  FileOptions options; options.use_direct_reads=true;
  Buffer scratch,expected; uint64_t full_bytes=0,samples=0;
  while(std::getline(manifest,line)) {
    auto a=line.find(','),b=line.find(',',a+1),c=line.find(',',b+1);
    require(a!=std::string::npos&&b!=std::string::npos&&c!=std::string::npos,"清单字段不全");
    auto file=line.substr(0,a); uint64_t size=std::stoull(line.substr(a+1,b-a-1)); unsigned seed=std::stoul(line.substr(b+1,c-b-1));
    bool filler=std::stoi(line.substr(c+1)); uint64_t actual=0; check(fs.GetFileSize(file,IOOptions(),&actual,nullptr)); require(actual==size,"只读校验长度不符");
    for(uint64_t i=0;i<kMiB;++i) expected.get()[i]=pattern(i,seed);
    std::unique_ptr<FSRandomAccessFile> reader; check(fs.NewRandomAccessFile(file,options,&reader,nullptr));
    uint64_t limit=filler?4096:size;
    for(uint64_t off=0;off<limit;off+=kMiB) {
      size_t bytes=filler?4096:kMiB; Slice result; check(reader->Read(off,bytes,IOOptions(),&result,scratch.get(),nullptr));
      require(result.size()==bytes&&std::memcmp(result.data(),expected.get(),bytes)==0,"只读校验数据不符");
    }
    if(filler) ++samples; else full_bytes+=size;
  }
  require(samples==kFillers,"填充抽检数量不符");
  std::cout<<"只读校验通过：业务完整比对字节="<<full_bytes<<"，填充文件首4KiB抽检="<<samples<<"，未启动维护线程"<<std::endl;
}
int main(int argc,char** argv) {
  try {
    require(argc==4,"用法：程序 run|verify 秒数 /home/femu/rrzc-stream-results/新目录");
    std::string mode=argv[1],out=argv[3]; unsigned seconds=std::stoul(argv[2]);
    require((mode=="run"||mode=="verify")&&seconds>=60&&seconds<=14400&&seconds%30==0,"模式或时长非法");
    require(seconds==180&&getenv("RRZC_TEST_MODEL_PROFILE")&&
            std::string(getenv("RRZC_TEST_MODEL_PROFILE"))=="competition"&&!getenv("RRZC_TEST_VICTIM_PROFILE"),
            "本轮仅支持显式competition固定180秒，不混用旧受控退役场景");
    require(out.rfind("/home/femu/rrzc-stream-results/",0)==0&&std::filesystem::is_directory(out),"不是隔离结果目录");
    require(!getenv("ZENFS_ZC_TEST_ENABLE")&&!getenv("ZENFS_ZC_TEST_SOURCE")&&!getenv("ZENFS_RRZC_MODE"),"禁止人工指定与旧趋势机制");
    require(getenv("ZENFS_RR_ENABLE")&&std::string(getenv("ZENFS_RR_ENABLE"))==(mode=="run"?"1":"0"),"RR开关不符");
    std::string logfile=out+"/zenfs."+mode+".log"; require(!std::filesystem::exists(logfile),"拒绝覆盖日志");
    std::shared_ptr<Logger> logger; check(Env::Default()->NewLogger(logfile,&logger)); logger->SetInfoLogLevel(DEBUG_LEVEL);
    bool readonly=mode=="verify";
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger); check(owned->Open(readonly,true));
    auto* device=owned.get(); auto fs=std::make_unique<ZenFS>(owned.release(),FileSystem::Default(),logger); check(fs->Mount(readonly));
    if(readonly) verify(*fs,out);
    else {
      require(getenv("ZENFS_RRZC_SIMPLE_MODE")&&(std::string(getenv("ZENFS_RRZC_SIMPLE_MODE"))=="valid"||std::string(getenv("ZENFS_RRZC_SIMPLE_MODE"))=="weighted"),"简单机制未启用");
      Stream load(*fs,*device,logger,seconds,out); load.run();
    }
    return 0;
  } catch(const std::exception& e) { std::cerr<<"滚动实验失败："<<e.what()<<std::endl; return 1; }
}
