// 合成文件生命周期夹具：编译实际观测方法；不访问NAND或执行RocksDB业务。
#include "rrzc_stream_profile.h"
#include <cassert>
#include <map>
#include <string>
#include <tuple>
#include <vector>
namespace rrzc_cp=rrzc_placement_trace;
struct Zone {
  uint64_t start_,generation=0;
  struct Generation {uint64_t generation;bool known=true;};
  Generation GetResetState(){return {generation,true};}
};
struct ZoneExtent {uint64_t start_,length_;Zone* zone_;};
struct Device {uint64_t id=rrzc_cp::NewId();uint64_t CompositionDevice(){return id;}};
struct ZoneFile {
  uint64_t composition_owner_=rrzc_cp::NewId();
  bool composition_temporary_=false,composition_owner_known_=false;
  uint64_t composition_size_=UINT64_MAX;
  std::array<uint64_t,6> composition_last_owner_{};
  using CompositionKey=std::tuple<uint64_t,uint64_t,uint64_t>;
  std::map<CompositionKey,uint64_t> composition_extents_;
  uint64_t file_id_=0,file_size_=0;
  rrzc_stream::Profile stream_profile_;
  std::atomic<bool> stream_sst_{true};
  bool flush_origin_w_=false,deleted=false;
  std::vector<std::string> linkfiles_{"合成.sst"};
  std::vector<ZoneExtent*> extents_;
  Device* zbd_;
  explicit ZoneFile(Device* z):zbd_(z){}
  bool IsDeleted(){return deleted;}
  void ObserveOwner();void ObserveAdd(ZoneExtent*);void ObserveClear();void ObserveMapped(uint64_t);
};
#include "真实观测方法.inc"

int main(){
  assert(rrzc_cp::Enabled());Device device;Zone one{4096},two{8192},three{12288};
  for(auto* zone:{&one,&two,&three})
    rrzc_cp::Emit(rrzc_cp::kZone,device.id,0,0,{zone->start_,0,4096,1});
  ZoneFile file(&device);file.file_id_=101;file.file_size_=1024;file.flush_origin_w_=true;
  ZoneExtent first{4096,1024,&one};file.extents_.push_back(&first);file.ObserveAdd(&first);
  auto target=[&](Zone& z,uint64_t native){rrzc_cp::Emit(rrzc_cp::kTarget,device.id,file.composition_owner_,rrzc_stream::Now(),
      {z.start_,z.generation,2048,native,0,3,1});};
  target(one,1024);
  for(unsigned i=0;i<256;++i)file.stream_profile_.Read(0,4096,1024);
  target(one,1024);
  // 先复制，再登记待提交引用；正式映射提交时才替换来源。
  const uint64_t tx=rrzc_cp::NewId();
  rrzc_cp::Emit(rrzc_cp::kCopy,device.id,file.composition_owner_,rrzc_stream::Now(),{tx,4096,4096,8192,8192,1024,0});
  rrzc_cp::Emit(rrzc_cp::kPendingRef,device.id,file.composition_owner_,rrzc_stream::Now(),{tx,8192,0,1024});
  target(two,1024);
  ZoneExtent second{8192,1024,&two};file.extents_={&second};file.ObserveMapped(tx);
  rrzc_cp::Emit(rrzc_cp::kTransactionEnd,device.id,file.composition_owner_,rrzc_stream::Now(),{tx,1});
  rrzc_cp::Emit(rrzc_cp::kVerifyBegin,device.id,file.composition_owner_,rrzc_stream::Now());
  rrzc_cp::Emit(rrzc_cp::kVerifyExtent,device.id,file.composition_owner_,rrzc_stream::Now(),{8192,0,8192,1024});
  rrzc_cp::Emit(rrzc_cp::kVerifyEnd,device.id,file.composition_owner_,rrzc_stream::Now());
  // 全局窗口推进但本文件无后续读，当前H应自然过期，历史H仍存在。
  for(unsigned i=0;i<65536;++i)rrzc_stream::Tick();
  target(two,1024);
  file.deleted=true;file.ObserveOwner();rrzc_cp::Emit(rrzc_cp::kRetire,device.id,file.composition_owner_,rrzc_stream::Now());
  target(two,1024);
  file.extents_.clear();file.ObserveClear();
  rrzc_cp::Emit(rrzc_cp::kReset,device.id,0,rrzc_stream::Now(),{4096,0,1,1});one.generation=1;
  ZoneFile next(&device);next.file_id_=102;next.file_size_=512;next.flush_origin_w_=true;
  ZoneExtent reused{4096,512,&one};next.extents_={&reused};next.ObserveAdd(&reused);
  // 临时恢复对象、非SST与未知类别都独立计量，不能冒充未分类SST。
  ZoneFile temp(&device);temp.file_id_=102;temp.composition_temporary_=true;
  temp.extents_={&reused};temp.ObserveAdd(&reused);
  ZoneFile meta(&device);meta.file_id_=103;meta.stream_sst_=false;meta.linkfiles_={"CURRENT"};
  ZoneExtent metadata{12288,16,&three};meta.extents_={&metadata};meta.ObserveAdd(&metadata);
  ZoneFile unknown(&device);unknown.stream_sst_=false;unknown.linkfiles_.clear();
  ZoneExtent opaque{12304,32,&three};unknown.extents_={&opaque};unknown.ObserveAdd(&opaque);
  rrzc_cp::Emit(rrzc_cp::kBoundary,device.id,0,rrzc_stream::Now(),{1,123});
  // 撤销的副本仍计复制字节，不能虚增有效映射或免除复制成本。
  const uint64_t canceled=rrzc_cp::NewId();
  rrzc_cp::Emit(rrzc_cp::kCopy,device.id,next.composition_owner_,rrzc_stream::Now(),{canceled,4096,4096,8192,8192,512,0});
  rrzc_cp::Emit(rrzc_cp::kTransactionEnd,device.id,next.composition_owner_,rrzc_stream::Now(),{canceled,0});
  for(auto* f:{&file,&next,&temp,&meta,&unknown}) {
    f->extents_.clear();f->ObserveClear();
    rrzc_cp::Emit(rrzc_cp::kOwnerEnd,device.id,f->composition_owner_,rrzc_stream::Now());
  }
}
