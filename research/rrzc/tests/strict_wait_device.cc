// 真实ZNS回归：开放输出与热文件同区；载荷.sst仅用于文件系统正确性，非RocksDB性能。
#include "plugin/zenfs/fs/fs_zenfs.h"
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <thread>
using namespace ROCKSDB_NAMESPACE;
static void check(const Status& s) {if (!s.ok()) throw std::runtime_error(s.ToString());}
static void require(bool ok,const char* why) {if (!ok) throw std::runtime_error(why);}
struct Buffer {
  char* data=nullptr;
  Buffer() {require(!posix_memalign(reinterpret_cast<void**>(&data),4096,1<<20),"对齐内存申请失败");std::memset(data,7,1<<20);}
  ~Buffer() {free(data);}
};
static uint64_t zone(ZenFS& fs,const std::string& name) {
  ZenFSSnapshot s;ZenFSSnapshotOptions o;o.zone_=o.zone_file_=true;fs.GetZenFSSnapshot(s,o);
  for (const auto& e:s.extents_) if (e.filename==name) return e.zone_start;
  throw std::runtime_error("未找到关闭文件映射");
}
int main(int argc,char** argv) {
  try {
    require(argc==2,"用法：程序 新日志路径");
    std::shared_ptr<Logger> logger;check(Env::Default()->NewLogger(argv[1],&logger));logger->SetInfoLogLevel(DEBUG_LEVEL);
    auto owned=std::make_unique<ZonedBlockDevice>("nvme0n1",ZbdBackendType::kBlockDev,logger);
    check(owned->Open(false,true));auto* d=owned.get();
    ZenFS fs(owned.release(),FileSystem::Default(),logger);check(fs.Mount(false));
    check(fs.CreateDir("/rr-wait",IOOptions(),nullptr));
    FileOptions o;o.use_direct_reads=o.use_direct_writes=true;
    Buffer data;
    std::unique_ptr<FSWritableFile> input,hot;
    check(fs.NewWritableFile("/rr-wait/input.sst",o,&input,nullptr));
    input->SetWriteLifeTimeHint(Env::WLTH_SHORT);
    // 当前设备写缓存条带为2MiB；写过条带才保证首页真的进入NAND计数路径。
    for (unsigned n=0;n<3;++n) check(input->Append(Slice(data.data,1<<20),IOOptions(),nullptr));
    check(input->Fsync(IOOptions(),nullptr));
    check(fs.NewWritableFile("/rr-wait/hot.sst",o,&hot,nullptr));
    // NOT_SET同寿命匹配差为0；LONG同寿命差为50且会优先开空区。
    hot->SetWriteLifeTimeHint(Env::WLTH_NOT_SET);
    check(hot->Append(Slice(data.data,1<<20),IOOptions(),nullptr));check(hot->Close(IOOptions(),nullptr));hot.reset();
    check(input->Close(IOOptions(),nullptr));input.reset();
    const auto source=zone(fs,"/rr-wait/hot.sst");
    require(source!=zone(fs,"/rr-wait/input.sst"),"输入未隔离，不能证明无关读取推进");
    const auto generation=d->GetIOZone(source)->GetResetState().generation;
    std::atomic<bool> ready{false},done{false};std::exception_ptr writer_error;
    std::thread writer([&]() {
      try {
        std::unique_ptr<FSWritableFile> output;
        check(fs.NewWritableFile("/rr-wait/output.sst",o,&output,nullptr));
        output->SetWriteLifeTimeHint(Env::WLTH_NOT_SET);
        for (unsigned n=0;n<2;++n) check(output->Append(Slice(data.data,1<<20),IOOptions(),nullptr));
        check(output->Fsync(IOOptions(),nullptr));
        // 热文件1MiB加开放输出2MiB，使首页落到NAND；快照仍跳过开放输出。
        require(d->GetIOZone(source)->used_capacity_==3U*(1<<20),"开放输出未与热表同区");
        ready=true;
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(10);
        bool threshold=false;uint64_t last_rc=0,configured_threshold=0;
        while (std::chrono::steady_clock::now()<deadline) {
          ZoneReadPressureReport p;check(d->GetZoneReadPressure(&p));
          configured_threshold=p.read_threshold;
          for (const auto& row:p.zones) if (row.start==source) {
            last_rc=row.max_block_reads;if (row.blocks_at_threshold) threshold=true;
          }
          if (threshold) break;
          std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (!threshold) std::cerr<<"阈值观测：源="<<source<<"，最大RC="<<last_rc<<"，配置阈值="<<configured_threshold<<std::endl;
        require(threshold,"未观测到真实1024次阈值");
        // 给触发线程时间进入覆盖不全的维护；旧版全局锁会阻塞下面读取。
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        Buffer scratch;std::unique_ptr<FSRandomAccessFile> reader;
        check(fs.NewRandomAccessFile("/rr-wait/input.sst",o,&reader,nullptr));Slice result;
        check(reader->Read(0,4096,IOOptions(),&result,scratch.data,nullptr));
        require(result.size()==4096 && !std::memcmp(result.data(),data.data,4096),"无关读取数据错误");
        check(output->Close(IOOptions(),nullptr));output.reset();done=true;
      } catch (...) {writer_error=std::current_exception();ready=true;}
    });
    std::exception_ptr read_error;
    try {
      while (!ready.load()) std::this_thread::yield();
      std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile("/rr-wait/hot.sst",o,&reader,nullptr));
      Buffer scratch;
      for (unsigned n=0;n<1024;++n) {
        Slice result;check(reader->Read(0,4096,IOOptions(),&result,scratch.data,nullptr));
        require(result.size()==4096 && !std::memcmp(result.data(),data.data,4096),"热读取数据错误");
      }
    } catch (...) {read_error=std::current_exception();}
    writer.join();if (read_error) std::rethrow_exception(read_error);if (writer_error) std::rethrow_exception(writer_error);
    require(done && zone(fs,"/rr-wait/hot.sst")!=source && zone(fs,"/rr-wait/output.sst")!=source &&
        d->GetIOZone(source)->GetResetState().generation==generation+1,"开放SST完成后未完整RR并重置源区");
    // 核验两份搬迁文件的完整3MiB内容，不只验证被重复读取的首页。
    for (const auto* name:{"/rr-wait/hot.sst","/rr-wait/output.sst"}) {
      std::unique_ptr<FSRandomAccessFile> reader;check(fs.NewRandomAccessFile(name,o,&reader,nullptr));Buffer scratch;Slice result;
      const unsigned parts=std::string(name)=="/rr-wait/output.sst" ? 2 : 1;
      for (unsigned n=0;n<parts;++n) {
        check(reader->Read(uint64_t(n)*(1<<20),1<<20,IOOptions(),&result,scratch.data,nullptr));
        require(result.size()==1<<20 && !std::memcmp(result.data(),data.data,1<<20),"RR后完整内容错误");
      }
    }
    std::cout<<"真实严格RR覆盖回归通过：1024次自然热读，开放同区输出等待无关读取后关闭，一次完整RR和源Reset，3MiB内容核验通过\n";
  } catch (const std::exception& e) {std::cerr<<"严格RR覆盖回归失败："<<e.what()<<'\n';return 1;}
}
