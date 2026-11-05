// 三种文件级受控先后关系；不是RocksDB SST、db_bench或YCSB性能实验。
#define main archived_colocation_functional_main
#include "colocation_device.cc"
#undef main
#include "rrzc_all_blocks_io.h"
#include "rrzc_front_order.h"
#include <array>
#include <chrono>
#include <endian.h>
#include <linux/fs.h>
#include <linux/blkzoned.h>
#include <thread>

constexpr uint64_t Z=128*MiB;
static const std::string H="/joint/H.sst",W="/joint/W.sst",C="/joint/C.sst";
static std::array<uint64_t,9> counters() {
  std::array<uint64_t,9> values{};nvme_admin_cmd command{};
  command.opcode=2;command.nsid=1;command.addr=reinterpret_cast<uint64_t>(values.data());
  command.data_len=72;command.cdw10=(17U<<16)|0xc3;
  const int fd=open("/dev/nvme0n1",O_RDONLY|O_CLOEXEC);require(fd>=0,"无法读取设备账目");
  const int code=ioctl(fd,NVME_IOCTL_ADMIN_CMD,&command);close(fd);require(!code,"C3查询失败");
  for(auto& value:values)value=le64toh(value);
  require(values[0]==1 && values[1]==1 && values[2]==values[3]+values[4],"C3启用或账目无效");return values;
}
class Input {
  std::ofstream out_;uint64_t sequence_=0;
 public:
  std::string phase="准备";
  explicit Input(const std::string& path):out_(path) {
    require(out_.good(),"请求日志不可写");out_<<"序号,阶段,状态,操作,文件,偏移,字节,参数,纳秒\n";
  }
  template<class F>void Call(const char* operation,const std::string& name,uint64_t offset,
                           uint64_t size,uint64_t parameter,F action) {
    const auto n=sequence_++;
    auto row=[&](const char* state) {
      out_<<n<<','<<phase<<','<<state<<','<<operation<<','<<name<<','<<offset<<','<<size<<','<<parameter<<','
          <<std::chrono::steady_clock::now().time_since_epoch().count()<<'\n';out_.flush();
    };
    row("提交");action();row("完成");
  }
};
static void Write(System& s,Input& input,const std::string& name,uint64_t bytes,char value,
                  Env::WriteLifeTimeHint hint) {
  Buffer buffer;std::memset(buffer.p,value,MiB);std::unique_ptr<FSWritableFile> writer;
  input.Call("CREATE",name,0,0,0,[&]{check(s.fs->NewWritableFile(name,options(),&writer,nullptr));});
  input.Call("HINT",name,0,0,static_cast<uint64_t>(hint),[&]{writer->SetWriteLifeTimeHint(hint);});
  for(uint64_t offset=0;offset<bytes;offset+=MiB) {
    const auto length=std::min<uint64_t>(MiB,bytes-offset);
    input.Call("APPEND",name,offset,length,static_cast<unsigned char>(value),
      [&]{check(writer->Append(Slice(buffer.p,length),IOOptions(),nullptr));});
  }
  input.Call("SYNC",name,0,0,0,[&]{check(writer->Fsync(IOOptions(),nullptr));});
  input.Call("CLOSE",name,0,0,0,[&]{check(writer->Close(IOOptions(),nullptr));});
}
struct Capture {
  System& system;std::shared_ptr<Logger> logger;std::string directory;
  std::ofstream space;
  Capture(System& s,std::shared_ptr<Logger> l,const std::string& out):
      system(s),logger(std::move(l)),directory(out),space(out+"/空间与设备.csv") {
    space<<"阶段,空闲,有效,满区可回收,实际NAND读取,计入RC读取,自身刷新豁免读取,其他块搬迁计数读取,阈值触发事件,被阻止读取,会话历史最高RC,采样Open,采样Active\n";
  }
  void Save(const std::string& stage) {
    const auto counts=counters();
    std::vector<char> bytes(sizeof(blk_zone_report)+64*sizeof(blk_zone));
    auto* physical=reinterpret_cast<blk_zone_report*>(bytes.data());physical->nr_zones=64;
    int fd=open("/dev/nvme0n1",O_RDONLY|O_CLOEXEC);require(fd>=0,"设备状态不可读");
    require(!ioctl(fd,BLKREPORTZONE,physical) && physical->nr_zones==64,"完整Zone状态读取失败");
    std::ofstream zones(directory+"/"+stage+".zones.csv");zones<<"Zone,WP扇区,条件\n";
    unsigned opened=0,active=0;
    for(unsigned i=0;i<64;++i) {
      const auto& zone=physical->zones[i];
      zones<<i<<','<<zone.wp<<','<<unsigned(zone.cond)<<'\n';
      const bool open=zone.cond==BLK_ZONE_COND_IMP_OPEN || zone.cond==BLK_ZONE_COND_EXP_OPEN;
      opened+=open;active+=open || zone.cond==BLK_ZONE_COND_CLOSED;
    }
    require(opened<=8 && active<=12,"实际Open/Active超过本轮上限");
    std::ofstream blocks(directory+"/"+stage+".blocks.csv");
    blocks<<"Zone,块,代际,Channel,LUN,Plane,物理块,RC,十秒增量,三十秒增量,标志,会话高,会话低\n";
    for(unsigned z=0;z<64;++z) {
      rrzc_all_blocks::Report report;std::string error;
      require(rrzc_all_blocks::Query(fd,1,64,128,z,Z,512,&report,&error),error.c_str());
      for(const auto& block:report.blocks) {
        require(block.rc<=1024,"物理RC超过严格阈值");
        blocks<<z<<','<<block.block_id<<','<<block.generation<<','<<block.ch<<','<<block.lun<<','
            <<block.plane<<','<<block.block<<','<<block.rc<<','<<block.delta10<<','<<block.delta30<<','
            <<block.flags<<','<<report.session_hi<<','<<report.session_lo<<'\n';
      }
    }
    close(fd);
    ZenFSSnapshot snapshot;ZenFSSnapshotOptions opt;opt.zone_=opt.zone_file_=true;
    system.fs->GetZenFSSnapshot(snapshot,opt);
    std::ofstream extents(directory+"/"+stage+".extents.csv");extents<<"文件,Zone,片段起点,字节\n";
    for(const auto& ext:snapshot.extents_)
      extents<<ext.filename<<','<<ext.zone_start/Z<<','<<ext.start<<','<<ext.length<<'\n';
    space<<stage<<','<<system.device->GetFreeSpace()<<','<<system.device->GetUsedSpace()<<','
        <<system.device->GetReclaimableSpace();
    for(size_t i=2;i<counts.size();++i)space<<','<<counts[i];
    space<<','<<opened<<','<<active<<'\n';space.flush();logger->Flush();
    std::cout<<"状态已保存："<<stage<<"，空闲="<<system.device->GetFreeSpace()<<std::endl;
  }
};
static void Read(std::unique_ptr<FSRandomAccessFile>& file,Input& input,Buffer& buffer,unsigned count) {
  for(unsigned i=0;i<count;++i)
    input.Call("READ",H,0,4096,0,[&]{Slice result;check(file->Read(0,4096,IOOptions(),&result,buffer.p,nullptr));
      require(result.size()==4096 && result[0]=='H',"热读内容错误");});
}
#ifndef RRZC_COLOCATION_CASES_HELPERS
int main(int argc,char** argv) {
  try {
    require(argc==4 && (std::string(argv[3])=="0" || std::string(argv[3])=="1"),
            "用法：受控情形 smoke|w-first|rr-first|cooled 结果目录 0|1");
    const std::string scenario=argv[1],out=argv[2];const bool pooled=std::string(argv[3])=="1";
    require(scenario=="smoke" || scenario=="w-first" || scenario=="rr-first" || scenario=="cooled","情形无效");
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(out+"/zenfs.log",&logger));
    logger->SetInfoLogLevel(DEBUG_LEVEL);System system(logger);Input input(out+"/请求.csv");
    Capture capture(system,logger,out);capture.Save("start");check(system.fs->CreateDir("/joint",IOOptions(),nullptr));
    Write(system,input,W,96*MiB,'W',Env::WLTH_SHORT);
    Write(system,input,H,32*MiB,'H',Env::WLTH_LONG);
    Write(system,input,C,96*MiB,'C',Env::WLTH_MEDIUM);
    const auto h=extent(system,H),w=extent(system,W),c=extent(system,C);
    require(pooled?(h.zone_start==w.zone_start && h.zone_start!=c.zone_start):
                   (h.zone_start!=w.zone_start && h.zone_start==c.zone_start),"未形成预注册的生命周期/协同布局");
    require(system.device->GetIOZone(h.zone_start)->IsFull(),"热读所在源区未自然写满");
    if(scenario=="smoke") {
      capture.Save("prepared");verify(system,H,32*MiB,'H');verify(system,W,96*MiB,'W');verify(system,C,96*MiB,'C');
      std::cout<<"布局最小测试通过；尚未验证GC收益\n";return 0;
    }
    for(unsigned i=0;i<52;++i) {
      Write(system,input,"/joint/filler"+std::to_string(i)+".bin",Z,static_cast<char>(40+i),Env::WLTH_NONE);
      if(i%8==7)std::cout<<"填充完成："<<i+1<<"/52"<<std::endl;
    }
    require(system.device->GetFreeSpace()==928*MiB && system.device->GetUsedSpace()==6880*MiB,
            "原20%及候选资格所需固定空间布局不符");
    std::cout<<"等待31秒使起始近期窗口成熟，不限业务QPS"<<std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(31));capture.Save("prepared");
    input.phase="业务";Info(logger,"受控放置业务开始：情形=%s，K=%d",scenario.c_str(),pooled);logger->Flush();
    const char* gate=getenv("ZENFS_RRZC_CAL_GC_GATE");require(gate && *gate,"缺少共同受控检查门");
    auto tick=[&](unsigned number){input.Call("CHECK","",number,0,0,[&]{rrzc_front_order::GcTick(gate,number);});};
    tick(1);
    std::unique_ptr<FSRandomAccessFile> reader;check(system.fs->NewRandomAccessFile(H,options(),&reader,nullptr));
    Buffer buffer;Read(reader,input,buffer,700);capture.Save("seed");
    if(scenario=="rr-first")Read(reader,input,buffer,512);
    input.Call("DELETE",W,0,0,0,[&]{check(system.fs->DeleteFile(W,IOOptions(),nullptr));});
    tick(2);capture.Save("after-maintenance");
    if(scenario=="w-first")Read(reader,input,buffer,512);
    tick(3);reader.reset();capture.Save("business");
    require(system.device->GetUsedSpace()==6784*MiB,"最终有效量不同于固定存活数据");
    require(system.device->GetFreeSpace()==1024*MiB && !system.device->GetReclaimableSpace(),
            "最终空间仍有维护负担，不发布净收益");
    Info(logger,"受控放置业务截止：内容校验另计");logger->Flush();input.phase="校验";
    verify(system,H,32*MiB,'H');verify(system,C,96*MiB,'C');
    for(unsigned i=0;i<52;++i) {
      verify(system,"/joint/filler"+std::to_string(i)+".bin",Z,static_cast<char>(40+i));
      if(i%8==7)std::cout<<"完整内容校验："<<i+1<<"/52"<<std::endl;
    }
    require(system.fs->FileExists(W,IOOptions(),nullptr).IsNotFound(),"W退役未生效");
    capture.Save("verified");std::cout<<"三情形受控验证完成："<<scenario<<"，K="<<pooled<<std::endl;
  }catch(const std::exception& error){std::cerr<<"受控情形失败："<<error.what()<<'\n';return 1;}
}
#endif
