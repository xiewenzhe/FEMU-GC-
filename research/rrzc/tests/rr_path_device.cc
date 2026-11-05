// 实验3：受控ZenFS文件读取验证RR真实路径；.sst仅作功能载荷，不是RocksDB表或性能负载。
#include "plugin/zenfs/fs/fs_zenfs.h"
#include <array>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <endian.h>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <iterator>
#include <linux/blkzoned.h>
#include <linux/nvme_ioctl.h>
#include <set>
#include <stdexcept>
#include <sys/ioctl.h>
#include <thread>
#include <unistd.h>
#include <vector>
using namespace ROCKSDB_NAMESPACE;
static constexpr uint64_t MiB=1ULL<<20;
static const std::string Name="/rr-path/hot.sst";
static void check(const Status& s) {if (!s.ok()) throw std::runtime_error(s.ToString());}
static void require(bool ok,const char* why) {if (!ok) throw std::runtime_error(why);}
struct Buffer {
  char* data=nullptr;
  Buffer() {require(!posix_memalign(reinterpret_cast<void**>(&data),4096,MiB),"对齐缓冲申请失败");std::memset(data,37,MiB);}
  ~Buffer() {free(data);}
};
static std::array<uint64_t,9> stats() {
  std::array<uint64_t,9> data{};nvme_admin_cmd cmd{};
  cmd.opcode=2;cmd.nsid=1;cmd.addr=reinterpret_cast<uint64_t>(data.data());cmd.data_len=72;
  cmd.cdw10=(17U<<16)|0xc3;
  const int fd=open("/dev/nvme0n1",O_RDONLY|O_CLOEXEC);
  require(fd>=0,"无法打开只读统计接口");const int result=ioctl(fd,NVME_IOCTL_ADMIN_CMD,&cmd);close(fd);
  require(result==0,"严格RR账目查询失败");for (auto& v:data) v=le64toh(v);
  require(data[0]==1 && data[1]==1 && data[2]==data[3]+data[4],"严格RR计数口径不符");return data;
}
static ZoneExtentSnapshot mapping(ZenFS& fs) {
  ZenFSSnapshot snap;ZenFSSnapshotOptions opt;opt.zone_=opt.zone_file_=true;fs.GetZenFSSnapshot(snap,opt);
  const ZoneExtentSnapshot* found=nullptr;
  for (const auto& e:snap.extents_) if (e.filename==Name) {
    require(found==nullptr,"受控文件出现多个片段，不能沿用单块身份验证");found=&e;
  }
  require(found && found->length==3*MiB,"受控文件映射或长度不符");return *found;
}
struct Trace {
  ZenFS& fs;ZonedBlockDevice& device;std::shared_ptr<Logger> logger;
  std::ofstream blocks,counts;std::set<uint64_t> visited;
  Trace(ZenFS& f,ZonedBlockDevice& d,std::shared_ptr<Logger> l,const std::string& out)
      :fs(f),device(d),logger(std::move(l)),blocks(out+"/块轨迹.csv"),counts(out+"/计数账目.csv") {
    require(blocks.good() && counts.good(),"无法创建轨迹文件");
    blocks<<"阶段,累计首页读取,当前映射Zone,当前片段起点,被观察Zone,设备代际,主机代际,最大块编号,Channel,LUN,Plane,Block,RC,十秒增量,三十秒增量,达阈值块数,读数总和,设备状态码,设备WP,主机有效字节\n";
    counts<<"阶段,版本,启用,实际NAND读取,计入RC读取,自身刷新豁免读取,其他块搬迁计数读取,阈值触发事件,被阻止读取,历史最高RC\n";
  }
  rrzc_v1::RecentBlock capture(const char* stage,uint64_t reads) {
    const auto ext=mapping(fs);visited.insert(ext.zone_start);
    // 同时保留当前空预留/替补的初始RC，验证后续目标没有继承旧源历史。
    const auto reserve=device.GetMigrationReserveObservation();
    const auto spare=device.GetMigrationSpareObservation();
    if (reserve.known && reserve.present) visited.insert(reserve.zone);
    if (spare.known && spare.present) visited.insert(spare.zone);
    rrzc_v1::RecentReport recent;check(device.GetZoneRecentReads(&recent));
    ZoneReadPressureReport pressure;check(device.GetZoneReadPressure(&pressure));
    require(recent.threshold==1024 && recent.blocks_per_zone==128 && recent.zones.size()==64 &&
        pressure.zones.size()==64,"实际设备组织或阈值不符");
    std::vector<uint64_t> storage((sizeof(blk_zone_report)+64*sizeof(blk_zone)+7)/8);
    auto* report=reinterpret_cast<blk_zone_report*>(storage.data());report->sector=0;report->nr_zones=64;
    const int fd=open("/dev/nvme0n1",O_RDONLY|O_CLOEXEC);require(fd>=0,"状态报告打开失败");
    const int result=ioctl(fd,BLKREPORTZONE,report);close(fd);require(result==0 && report->nr_zones==64,"状态报告失败");
    rrzc_v1::RecentBlock current;
    for (const auto zone:visited) {
      const auto index=zone/(128*MiB);require(index<64,"Zone地址超界");
      const auto& b=recent.zones[index];const auto& p=pressure.zones[index];const auto& z=report->zones[index];
      auto* host=device.GetIOZone(zone);require(host && b.zone==zone && p.start==zone,"跨层地址不一致");
      blocks<<stage<<','<<reads<<','<<ext.zone_start<<','<<ext.start<<','<<zone<<','<<b.generation<<','
        <<host->GetResetState().generation<<','<<b.block_id<<','<<b.ch<<','<<b.lun<<','<<b.plane<<','<<b.block<<','
        <<b.rc<<','<<b.delta10<<','<<b.delta30<<','<<p.blocks_at_threshold<<','<<p.sum_block_reads<<','
        <<static_cast<unsigned>(z.cond)<<','<<z.wp*512<<','<<host->used_capacity_.load()<<'\n';
      if (zone==ext.zone_start) current=b;
    }
    const auto c=stats();counts<<stage;for (const auto v:c) counts<<','<<v;counts<<'\n';
    blocks.flush();counts.flush();require(blocks.good() && counts.good(),"轨迹写入失败");
    Info(logger,"RR路径采样：阶段=%s，累计首页读取=%lu，映射Zone=%lu，块=%u，设备代际=%lu，RC=%lu，阈值事件=%lu",
         stage,reads,ext.zone_start,current.block_id,current.generation,current.rc,c[6]);logger->Flush();
    std::cout<<"RR路径采样："<<stage<<"，首页读="<<reads<<"，Zone="<<ext.zone_start/(128*MiB)
      <<"，块="<<current.block_id<<"，代际="<<current.generation<<"，RC="<<current.rc<<std::endl;
    return current;
  }
};
int main(int argc,char** argv) {
  try {
    require(argc==2,"用法：程序 独立结果目录");const std::string out=argv[1];
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(out+"/rr.zenfs.log",&logger));logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);
    check(owned->Open(false,true));auto* device=owned.get();
    ZenFS fs(owned.release(),FileSystem::Default(),logger);check(fs.Mount(false));check(fs.CreateDir("/rr-path",IOOptions(),nullptr));
    FileOptions opt;opt.use_direct_reads=opt.use_direct_writes=true;Buffer expected,scratch;
    std::unique_ptr<FSWritableFile> writer;check(fs.NewWritableFile(Name,opt,&writer,nullptr));
    writer->SetWriteLifeTimeHint(Env::WLTH_NOT_SET);
    // 写过当前2MiB设备缓存条带，保证首页真实访问NAND，而非仅在设备写缓存命中。
    for (unsigned i=0;i<3;++i) check(writer->Append(Slice(expected.data,MiB),IOOptions(),nullptr));
    check(writer->Fsync(IOOptions(),nullptr));check(writer->Close(IOOptions(),nullptr));writer.reset();
    // 等已有影子日志开关就绪；只用它记录复制/提交，GC已在超级块中关闭。
    const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(3);bool ready=false;
    while (std::chrono::steady_clock::now()<until) {
      logger->Flush();std::ifstream log(out+"/rr.zenfs.log");
      std::string all((std::istreambuf_iterator<char>(log)),std::istreambuf_iterator<char>());
      if (all.find("V1影子：已启用")!=std::string::npos) {ready=true;break;}
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    require(ready,"已有复制/提交诊断没有启用");Trace trace(fs,*device,logger,out);
    const auto original=trace.capture("装载后",0);require(original.rc==0,"首页初始RC不是零");
    const auto before=stats();uint64_t reads=0;
    std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile(Name,opt,&reader,nullptr));
    auto read_one=[&]() {
      Slice result;check(reader->Read(0,4096,IOOptions(),&result,scratch.data,nullptr));
      require(result.size()==4096 && !std::memcmp(result.data(),expected.data,4096),"首页内容错误");++reads;
    };
    for (const unsigned stop:{1U,128U,512U,1023U}) {
      while (reads<stop) {read_one();}
      const auto b=trace.capture(("读"+std::to_string(stop)).c_str(),reads);
      require(b.zone==original.zone && b.generation==original.generation && b.block_id==original.block_id && b.rc==stop,
          "阈值前不是同一物理块逐次真实计数");
    }
    read_one();const auto first=trace.capture("首次RR后",reads);
    // 当前库完成维护后重读触发请求；API返回时新块已产生一次真实读，故应为1。
    require(first.zone!=original.zone && first.rc==1,"第1024读后映射或新位置重读计数不符");
    require(device->GetIOZone(original.zone)->IsEmpty(),"首次RR源区未Reset");
    require(stats()[6]==before[6]+1,"首次RR阈值事件数不符");
    for (unsigned i=0;i<32;++i) {read_one();}
    const auto warm=trace.capture("新块追加32读",reads);
    require(warm.zone==first.zone && warm.generation==first.generation && warm.block_id==first.block_id && warm.rc==33,
        "新物理块没有从一次重读继续计数");
    const auto idle_before=stats();std::this_thread::sleep_for(std::chrono::seconds(12));
    const auto idle=trace.capture("空闲12秒后",reads);
    require(idle.zone==warm.zone && idle.generation==warm.generation && idle.rc==33 && stats()[6]==idle_before[6],
        "无新读取期间发生重复触发或计数变化");
    const uint64_t before_second=1024+(1023-first.rc);
    while (reads<before_second) {read_one();}
    const auto near=trace.capture("第二次阈值前",reads);
    require(near.zone==first.zone && near.generation==first.generation && near.block_id==first.block_id && near.rc==1023,
        "第二次触发前块身份或RC不符");
    read_one();const auto second=trace.capture("第二次RR后",reads);
    require(second.zone!=first.zone && second.rc==1 && device->GetIOZone(first.zone)->IsEmpty(),"第二次真实RR或新位置重读没有完成");
    require(stats()[6]==before[6]+2,"两次独立阈值事件不符");
    // 核验完整3MiB文件，不只核验反复读取的首页；此处真实读取另计入RC。
    for (unsigned i=0;i<3;++i) {
      Slice result;check(reader->Read(i*MiB,MiB,IOOptions(),&result,scratch.data,nullptr));
      require(result.size()==MiB && !std::memcmp(result.data(),expected.data,MiB),"RR后完整内容错误");
    }
    trace.capture("完整内容校验后",reads);reader.reset();
    require(stats()[6]==before[6]+2 && stats()[8]==1024,"校验导致意外RR或严格阈值越界");
    std::cout<<"纯RR路径验证通过：两次自然1024阈值，旧源Reset，新块重新计数，12秒空闲无重复，完整3MiB内容正确\n";
  } catch (const std::exception& e) {std::cerr<<"RR路径验证失败："<<e.what()<<'\n';return 1;}
}
