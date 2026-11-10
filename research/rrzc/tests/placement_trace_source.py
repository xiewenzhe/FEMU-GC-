"""第二阶段仅向阶段一独立副本插入事件；不改变模式、映射或持久化分支。"""
from calibration_source import once

NAMESPACE='namespace rrzc_cp = rrzc_placement_trace;\n'


def profile(text):
    text=once(text,'#pragma once','#pragma once\n#include "rrzc_placement_trace.h"')
    text=once(text,'  Stats Snapshot(uint64_t file_bytes=0) const {',
        '  uint64_t ObservationId() const {return projection_.Id();}\n'
        '  void ObserveSnapshot(uint64_t file_bytes) {\n'
        '    if(!rrzc_placement_trace::Enabled())return;\n'
        '    std::lock_guard<std::mutex> guard(mutex_);const auto event=Now();\n'
        '    projection_.Observe(event,file_bytes,SnapshotLocked(event,file_bytes),buckets_,false,0,false);\n'
        '  }\n'
        '  Stats Snapshot(uint64_t file_bytes=0) const {')
    text=once(text,'    reported_hot_=result.hot;return result;',
        '    reported_hot_=result.hot;\n'
        '    projection_.Observe(event,file_bytes,result,buckets_,reuse,bytes);\n'
        '    return result;')
    return once(text,'  bool reported_hot_=false;',
        '  bool reported_hot_=false;\n  rrzc_placement_trace::HeatProjection projection_;')


def scope(text):
    text=once(text,'std::set<uint64_t> sources;','std::set<uint64_t> sources;uint64_t owner=0;')
    text=once(text,'std::set<uint64_t> sources={}):old_',
        'std::set<uint64_t> sources={},uint64_t owner=0):old_')
    return once(text,'current={static_cast<Tag>(tag),std::move(sources)};',
        'current={static_cast<Tag>(tag),std::move(sources),owner};')


def zbd_header(text):
    text=once(text,'#include "rrzc_placement_mode.h"',
        '#include "rrzc_placement_mode.h"\n#include "rrzc_placement_trace.h"')
    text=once(text,'  rrzc_placement::Mode placement_mode_=',
        '  uint64_t composition_device_=rrzc_placement_trace::NewId();\n'
        '  rrzc_placement::Mode placement_mode_=')
    return once(text,'  rrzc_colocation::Pool* ColocationPool()',
        '  uint64_t CompositionDevice() const {return composition_device_;}\n'
        '  rrzc_colocation::Pool* ColocationPool()')


IO_MEMBERS='''  uint64_t composition_owner_=rrzc_placement_trace::NewId();
  bool composition_temporary_=false;
  bool composition_owner_known_=false;
  uint64_t composition_size_=UINT64_MAX;
  std::array<uint64_t,6> composition_last_owner_{};
  using CompositionKey=std::tuple<uint64_t,uint64_t,uint64_t>;
  std::map<CompositionKey,uint64_t> composition_extents_;
'''
IO_METHODS='''  uint64_t CompositionOwner() const {return composition_owner_;}
  void ObserveOwner();
  void ObserveAdd(ZoneExtent* ext);
  void ObserveClear();
  void ObserveMapped(uint64_t transaction);
  void ObservePrimary() {composition_temporary_=false;ObserveOwner();}
'''


def io_header(text):
    text=once(text,'#include "rrzc_stream_profile.h"',
        '#include "rrzc_stream_profile.h"\n#include <map>\n#include <tuple>')
    text=once(text,'  rrzc_stream::Profile stream_profile_;',IO_MEMBERS+'  rrzc_stream::Profile stream_profile_;')
    text=once(text,'  void MaybeCaptureFlushOrigin();',IO_METHODS+'  void MaybeCaptureFlushOrigin();')
    return once(text,'void SetDeleted() { is_deleted_ = true; }',
        'void SetDeleted() { is_deleted_ = true;\n'
        '    rrzc_placement_trace::Emit(rrzc_placement_trace::kRetire,zbd_->CompositionDevice(),composition_owner_,rrzc_stream::Now()); }')


IO_IMPL='''
// 这些观测只在原文件写/恢复/迁移同步点读取本对象extent，不跨文件扫描。
void ZoneFile::ObserveOwner() {
  if(!rrzc_cp::Enabled())return;
  if(composition_size_!=file_size_) {
    stream_profile_.ObserveSnapshot(file_size_);composition_size_=file_size_;
  }
  const std::array<uint64_t,6> description{file_id_,stream_profile_.ObservationId(),
      stream_sst_.load()?1UL:linkfiles_.empty()?0UL:2UL,flush_origin_w_,composition_temporary_,IsDeleted()};
  if(composition_owner_known_ && description==composition_last_owner_)return;
  composition_last_owner_=description;composition_owner_known_=true;
  rrzc_cp::Emit(rrzc_cp::kOwner,zbd_->CompositionDevice(),composition_owner_,rrzc_stream::Now(),
      {file_id_,stream_profile_.ObservationId(),stream_sst_.load()?1UL:linkfiles_.empty()?0UL:2UL,flush_origin_w_,
       composition_temporary_,IsDeleted(),file_size_});
}
void ZoneFile::ObserveAdd(ZoneExtent* ext) {
  if(!rrzc_cp::Enabled())return;
  ObserveOwner();
  const auto generation=ext->zone_->GetResetState();
  const CompositionKey key{ext->zone_->start_,generation.generation,ext->start_};
  composition_extents_[key]+=ext->length_;
  rrzc_cp::Emit(rrzc_cp::kAdd,zbd_->CompositionDevice(),composition_owner_,rrzc_stream::Now(),
      {ext->zone_->start_,generation.generation,ext->start_,ext->length_,generation.known});
}
void ZoneFile::ObserveClear() {
  if(!rrzc_cp::Enabled())return;
  ObserveOwner();composition_extents_.clear();
  rrzc_cp::Emit(rrzc_cp::kClear,zbd_->CompositionDevice(),composition_owner_,rrzc_stream::Now());
}
void ZoneFile::ObserveMapped(uint64_t transaction) {
  if(!rrzc_cp::Enabled())return;
  ObserveOwner();
  std::map<CompositionKey,uint64_t> actual;
  for(auto* ext:extents_)actual[{ext->zone_->start_,ext->zone_->GetResetState().generation,ext->start_}]+=ext->length_;
  rrzc_cp::Emit(rrzc_cp::kMapBegin,zbd_->CompositionDevice(),composition_owner_,rrzc_stream::Now(),{transaction});
  for(const auto& item:composition_extents_)if(!actual.count(item.first) || actual.at(item.first)!=item.second)
    rrzc_cp::Emit(rrzc_cp::kRemove,zbd_->CompositionDevice(),composition_owner_,rrzc_stream::Now(),
        {std::get<0>(item.first),std::get<1>(item.first),std::get<2>(item.first),item.second});
  for(const auto& item:actual)if(!composition_extents_.count(item.first) || composition_extents_.at(item.first)!=item.second)
    rrzc_cp::Emit(rrzc_cp::kAdd,zbd_->CompositionDevice(),composition_owner_,rrzc_stream::Now(),
        {std::get<0>(item.first),std::get<1>(item.first),std::get<2>(item.first),item.second,1});
  composition_extents_=std::move(actual);
  rrzc_cp::Emit(rrzc_cp::kMapEnd,zbd_->CompositionDevice(),composition_owner_,rrzc_stream::Now(),{transaction});
}
'''

# 观测镜像分配失败只使证据无效，绝不向原有映射/分配调用抛出新异常。
for _method,_argument in [('ObserveOwner',''),('ObserveAdd','ZoneExtent* ext'),
                          ('ObserveClear',''),('ObserveMapped','uint64_t transaction')]:
    _begin='void ZoneFile::'+_method+'('+_argument+') {'
    _start=IO_IMPL.index(_begin)
    _end=IO_IMPL.index('\n}',_start)
    IO_IMPL=IO_IMPL[:_end]+'\n  } catch(...) {rrzc_cp::Invalidate();}'+IO_IMPL[_end:]
    IO_IMPL=IO_IMPL[:_start]+IO_IMPL[_start:].replace(_begin,_begin+'\n  try {',1)


def io(text):
    text='#include "rrzc_placement_trace.h"\n'+NAMESPACE+text
    text=once(text,'void ZoneFile::SetFileSize(uint64_t sz) { file_size_ = sz; }',
        'void ZoneFile::SetFileSize(uint64_t sz) { file_size_ = sz; ObserveOwner(); }')
    text=once(text,'ZoneFile::~ZoneFile() { ClearExtents(); }',IO_IMPL+'''
ZoneFile::~ZoneFile() { ClearExtents();
  rrzc_cp::Emit(rrzc_cp::kOwnerEnd,zbd_->CompositionDevice(),composition_owner_,rrzc_stream::Now()); }
''')
    text=once(text,'  extents_.clear();','  extents_.clear();\n  ObserveClear();')
    text=once(text,'Status ZoneFile::DecodeFrom(Slice* input) {',
        'Status ZoneFile::DecodeFrom(Slice* input) {\n  composition_temporary_=true;')
    text=once(text,'        extents_.push_back(extent);',
        '        extents_.push_back(extent);\n        ObserveAdd(extent);')
    text=once(text,'  MetadataSynced();\n  return Status::OK();',
        '  MetadataSynced();\n  ObserveOwner();\n  return Status::OK();')
    text=once(text,'    extents_.push_back(new ZoneExtent(extent->start_, extent->length_, zone));',
        '    extents_.push_back(new ZoneExtent(extent->start_, extent->length_, zone));\n    ObserveAdd(extents_.back());')
    text=once(text,'  stream_sst_=stream_sst_.load() || update->stream_sst_.load();',
        '  stream_sst_=stream_sst_.load() || update->stream_sst_.load();\n  ObserveOwner();')
    # 新增extent必须位于原生有效计数增加之后，而不是目标申请或物理Append之前。
    for anchor in ('  active_zone_->used_capacity_ += length;', '    active_zone_->used_capacity_ += extent_length;\n    file_size_ += extent_length;'):
        expected=1 if anchor.startswith('  active') else 2
        if text.count(anchor)!=expected:raise ValueError('原生extent增加锚点变化')
        text=text.replace(anchor,anchor+'\n'+('  ' if expected==1 else '    ')+'ObserveAdd(extents_.back());')
    text=once(text,'    file_size_ += wr_size;',
        '    file_size_ += wr_size;\n    ObserveOwner();')
    for anchor in ('    extents_.push_back(new ZoneExtent(next_extent_start + SPARSE_HEADER_SIZE,\n                                      extent_length, zone));',
                   '    extents_.push_back(new ZoneExtent(extent_start_, to_recover, zone));'):
        text=once(text,anchor,anchor+'\n    ObserveAdd(extents_.back());')
    text=once(text,'  MaybeCaptureFlushOrigin();\n  rrzc_colocation::Scope colocation(ColocationTag());',
        '  MaybeCaptureFlushOrigin();\n  ObserveOwner();\n'
        '  rrzc_colocation::Scope colocation(ColocationTag(),{},composition_owner_);')
    return once(text,'  SetActiveZone(zone);','''  SetActiveZone(zone);
  if(rrzc_cp::Enabled()) {
    const auto generation=zone->GetResetState();
    rrzc_cp::Emit(rrzc_cp::kTarget,zbd_->CompositionDevice(),composition_owner_,rrzc_stream::Now(),
        {zone->start_,generation.generation,zone->capacity_,zone->used_capacity_.load(),1,static_cast<uint8_t>(rrzc_colocation::current.tag),generation.known});
  }''')


def fs(text):
    text='#include "rrzc_placement_trace.h"\n'+NAMESPACE+text
    text=once(text,'  s = zone_->Append(buffer, phys_sz);',
        '''  s = zone_->Append(buffer, phys_sz);
  rrzc_cp::Emit(rrzc_cp::kMetadataWrite,zbd_->CompositionDevice(),0,rrzc_stream::Now(),
      {zone_->start_,zone_->GetResetState().generation,zone_->wp_,s.ok(),record_sz});''')
    text=once(text,'  zoneFile->ReplaceExtentList(new_extents);',
        '  zoneFile->ReplaceExtentList(new_extents);')
    anchor='    delete old_ext;\n  }\n\n  return IOStatus::OK();'
    text=once(text,anchor,'    delete old_ext;\n  }\n  zoneFile->ObserveMapped(0);\n\n  return IOStatus::OK();')
    text=once(text,'      s = zFile->MergeUpdate(update, replace);',
        '      zFile->ObservePrimary();\n      s = zFile->MergeUpdate(update, replace);')
    text=once(text,'  files_.insert(std::make_pair(update->GetFilename(), update));',
        '  update->ObservePrimary();\n  files_.insert(std::make_pair(update->GetFilename(), update));')
    text=once(text,'    if (zoneFile->GetID() >= next_file_id_)',
        '    zoneFile->ObservePrimary();\n    if (zoneFile->GetID() >= next_file_id_)')
    text=once(text,'  Info(logger_,"前后台重叠维护边界：阶段=%lu，请求=%lu，单调纳秒=%lu",stage,operations,captured_ns);',
        '''  rrzc_cp::Emit(rrzc_cp::kBoundary,zbd_->CompositionDevice(),0,rrzc_stream::Now(),{stage,operations});
  Info(logger_,"前后台重叠维护边界：阶段=%lu，请求=%lu，单调纳秒=%lu",stage,operations,captured_ns);''')
    text=once(text,'      if (!file.TryAcquireWRLock()) continue;', '''      if (!file.TryAcquireWRLock()) {
        rrzc_cp::Emit(rrzc_cp::kVerifySkip,zbd_->CompositionDevice(),file.CompositionOwner(),rrzc_stream::Now());
        continue;
      }
      if(rrzc_cp::Enabled()) {
        file.ObserveOwner();
        rrzc_cp::Emit(rrzc_cp::kVerifyBegin,zbd_->CompositionDevice(),file.CompositionOwner(),rrzc_stream::Now());
        for(auto* ext:file.GetExtents())
          rrzc_cp::Emit(rrzc_cp::kVerifyExtent,zbd_->CompositionDevice(),file.CompositionOwner(),rrzc_stream::Now(),
              {ext->zone_->start_,ext->zone_->GetResetState().generation,ext->start_,ext->length_});
        rrzc_cp::Emit(rrzc_cp::kVerifyEnd,zbd_->CompositionDevice(),file.CompositionOwner(),rrzc_stream::Now());
      }''')
    start=text.index('IOStatus ZenFS::MigrateFileExtents(')
    prefix,body=text[:start],text[start:]
    body=once(body,'  bool changed = false;',
        '  bool changed = false;\n  const uint64_t composition_transaction=rrzc_cp::NewId();\n  zfile->ObserveOwner();')
    body=once(body,'rrzc_colocation::Scope colocation(zfile->ColocationTag(),source_zones_for_colocation);',
        'rrzc_colocation::Scope colocation(zfile->ColocationTag(),source_zones_for_colocation,zfile->CompositionOwner());')
    body=once(body,'    if (migration_window_.Enabled())\n      migration_scopes.push_back(migration_window_.Mark({target_zone->start_}));',
        '''    if(rrzc_cp::Enabled()) {
      const auto generation=target_zone->GetResetState();
      rrzc_cp::Emit(rrzc_cp::kTarget,zbd_->CompositionDevice(),zfile->CompositionOwner(),rrzc_stream::Now(),
          {target_zone->start_,generation.generation,target_zone->capacity_,target_zone->used_capacity_.load(),0,static_cast<uint8_t>(rrzc_colocation::current.tag),generation.known});
    }
    if (migration_window_.Enabled())
      migration_scopes.push_back(migration_window_.Mark({target_zone->start_}));''')
    body=once(body,'    // 文件可能在迁移期间被删除，此时不更新原文件的映射。',
        '''    rrzc_cp::Emit(rrzc_cp::kCopy,zbd_->CompositionDevice(),zfile->CompositionOwner(),rrzc_stream::Now(),
        {composition_transaction,observed_source,observed_source_start,target_zone->start_,target_start,ext->length_,target_zone->GetResetState().generation});
    // 文件可能在迁移期间被删除，此时不更新原文件的映射。''')
    body=once(body,'    ext->zone_->used_capacity_ += ext->length_;',
        '''    ext->zone_->used_capacity_ += ext->length_;
    rrzc_cp::Emit(rrzc_cp::kPendingRef,zbd_->CompositionDevice(),zfile->CompositionOwner(),rrzc_stream::Now(),
        {composition_transaction,target_zone->start_,target_zone->GetResetState().generation,ext->length_});''')
    body=once(body,'    if (!sync_status.ok()) s = sync_status;',
        '''    rrzc_cp::Emit(sync_status.ok()?rrzc_cp::kTransactionEnd:rrzc_cp::kUncertain,
        zbd_->CompositionDevice(),zfile->CompositionOwner(),rrzc_stream::Now(),{composition_transaction,1});
    if (!sync_status.ok()) s = sync_status;''')
    body=once(body,'  } else {\n    for (auto* ext : new_extent_list) delete ext;',
        '''  } else {
    rrzc_cp::Emit(rrzc_cp::kTransactionEnd,zbd_->CompositionDevice(),zfile->CompositionOwner(),rrzc_stream::Now(),{composition_transaction,0});
    for (auto* ext : new_extent_list) delete ext;''')
    return prefix+body


def zbd(text):
    text='#include "rrzc_placement_trace.h"\n'+NAMESPACE+text
    text=once(text,'        meta_zones.push_back(new Zone(this, zbd_be_.get(), zone_rep, i));',
        '''        meta_zones.push_back(new Zone(this, zbd_be_.get(), zone_rep, i));
        rrzc_cp::Emit(rrzc_cp::kZoneRole,CompositionDevice(),0,rrzc_stream::Now(),
            {meta_zones.back()->start_,meta_zones.back()->GetResetState().generation,1,meta_zones.back()->wp_});''')
    text=once(text,'        io_zones.push_back(newZone);',
        '''        io_zones.push_back(newZone);
        rrzc_cp::Emit(rrzc_cp::kZoneRole,CompositionDevice(),0,rrzc_stream::Now(),
            {newZone->start_,newZone->GetResetState().generation,0,newZone->wp_});''')
    text=once(text,'  used_capacity_ = 0;', '''  used_capacity_ = 0;
  rrzc_cp::Emit(rrzc_cp::kZone,zbd_->CompositionDevice(),0,rrzc_stream::Now(),
      {start_,GetResetState().generation,max_capacity_,GetResetState().known});''')
    anchor='  zbd_->LogZoneReset(GetResetState(), previous_generation, old_wp, ios, offline);'
    text=once(text,'    zbd_->LogZoneReset(GetResetState(), previous_generation, old_wp, ios, false);',
        '''    zbd_->LogZoneReset(GetResetState(), previous_generation, old_wp, ios, false);
    rrzc_cp::Emit(rrzc_cp::kUncertain,zbd_->CompositionDevice(),0,rrzc_stream::Now(),{start_,previous_generation,0});''')
    return once(text,anchor,anchor+'''
  rrzc_cp::Emit(rrzc_cp::kReset,zbd_->CompositionDevice(),0,rrzc_stream::Now(),
      {start_,previous_generation,GetResetState().generation,1});''')


def adapter(text):
    text=once(text,'  placement_mode_=placement.mode;', '''  placement_mode_=placement.mode;
  rrzc_cp::Emit(rrzc_cp::kMode,CompositionDevice(),0,rrzc_stream::Now(),{static_cast<uint64_t>(placement_mode_)});''')
    text=once(text,'  *output=nullptr;\n','''  *output=nullptr;
  rrzc_cp::Emit(rrzc_cp::kAttempt,CompositionDevice(),rrzc_colocation::current.owner,rrzc_stream::Now(),
      {static_cast<uint8_t>(rrzc_colocation::current.tag),own_open,required,static_cast<uint64_t>(placement_mode_)});
''')
    text=once(text,'    return IOStatus::OK();\n  }\n  if(!coloc_pool_',
        '''    rrzc_cp::Emit(rrzc_cp::kG1Skip,CompositionDevice(),rrzc_colocation::current.owner,rrzc_stream::Now(),
        {static_cast<uint8_t>(rrzc_colocation::current.tag)});
    return IOStatus::OK();
  }
  if(!coloc_pool_''')
    return once(text,'  *output=z;return IOStatus::OK();', '''  rrzc_cp::Emit(rrzc_cp::kPoolTarget,CompositionDevice(),rrzc_colocation::current.owner,rrzc_stream::Now(),
      {z->start_,z->GetResetState().generation,z->capacity_,z->used_capacity_.load(),own_open,static_cast<uint8_t>(plan.tag),fresh});
  *output=z;return IOStatus::OK();''')


TRANSFORMS={'rrzc_stream_profile.h':profile,'rrzc_colocation_scope.h':scope,
    'zbd_zenfs.h':zbd_header,'io_zenfs.h':io_header,'io_zenfs.cc':io,
    'fs_zenfs.cc':fs,'zbd_zenfs.cc':zbd,'rrzc_colocation_zenfs.inc':adapter}
