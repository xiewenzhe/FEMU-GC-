"""仅生成独立ZenFS副本；K=1使用可恢复文件元数据标签10，旧库/镜像不改。"""
from calibration_source import once


def zbd_header(source):
    text=once(source,'#pragma once','#pragma once\n#include "rrzc_colocation_scope.h"\n#include <fstream>')
    text=once(text,'  bool maintenance_diag_enabled_ = false;',
        '  bool maintenance_diag_enabled_ = false;\n'
        '  std::unique_ptr<rrzc_colocation::Pool> coloc_pool_;\n'
        '  std::map<std::string,uint8_t> coloc_tags_;\n'
        '  std::set<Zone*> coloc_migration_targets_;\n'
        '  void InitColocationPool();\n'
        '  std::vector<rrzc_colocation::Zone> ColocationObservation(bool risk);\n'
        '  IOStatus TryColocation(uint32_t,Env::WriteLifeTimeHint,bool,Zone**);')
    return once(text,'  Zone *GetIOZone(uint64_t offset);',
        '  Zone *GetIOZone(uint64_t offset);\n'
        '  rrzc_colocation::Pool* ColocationPool() const {return coloc_pool_.get();}\n'
        '  uint8_t ColocationTag(const std::string& name) const;\n'
        '  IOStatus RestoreColocation(const std::map<uint64_t,uint8_t>& roles);')


def zbd_source(source):
    text=once(source,'  rrzc_content_.store(rrzc_content::kUnknown);',
        '  rrzc_content_.store(rrzc_content::kUnknown);\n'
        '  if(zbd_->ColocationPool())zbd_->ColocationPool()->Reset(start_);')
    text=once(text,'  Info(logger_, "Zone block device nr zones:',
        '  InitColocationPool();\n\n  Info(logger_, "Zone block device nr zones:')
    # 普通路径不能把无标签文件写进协同池；关闭功能时原扫描原样运行。
    text=once(text,'      if ((z->used_capacity_ > 0) && !z->IsFull() &&',
        '      if ((z->used_capacity_ > 0) && !z->IsFull() &&\n'
        '          (!coloc_pool_ || !coloc_pool_->Contains(z->start_)) &&')
    text=once(text,'  WaitForOpenIOZoneToken(io_type == IOType::kWAL);',
        '  WaitForOpenIOZoneToken(io_type == IOType::kWAL);\n'
        '  if(coloc_pool_ && io_type!=IOType::kWAL && content==rrzc_content::kSst) {\n'
        '    const auto status=TryColocation(1,file_lifetime,true,out_zone);\n'
        '    if(!status.ok()){PutOpenIOZoneToken();return status;}\n'
        '    if(*out_zone)return IOStatus::OK();\n'
        '  }')
    text=once(text,'  migrating_ = true;\n\n  unsigned int best_diff',
        '  migrating_ = true;\n'
        '  if(coloc_pool_) {\n'
        '    const auto status=TryColocation(min_capacity,file_lifetime,false,out_zone);\n'
        '    if(!status.ok()){migrating_=false;migrate_resource_.notify_one();return status;}\n'
        '    if(*out_zone)return IOStatus::OK();\n'
        '  }\n\n  unsigned int best_diff')
    text=once(text,'    migrating_ = false;\n    if (zone != nullptr) {',
        '    migrating_ = false;\n'
        '    if(zone && coloc_migration_targets_.erase(zone)) {\n'
        '      s=zone->Close();\n'
        '      if(zone->IsFull())PutActiveIOZoneToken();\n'
        '      const auto release=zone->CheckRelease();\n'
        '      PutOpenIOZoneToken();migrate_resource_.notify_one();\n'
        '      return s.ok()?release:s;\n'
        '    }\n    if (zone != nullptr) {')
    return once(text,'IOStatus ZonedBlockDevice::AllocateIOZone(',
        '#include "rrzc_colocation_zenfs.inc"\n\nIOStatus ZonedBlockDevice::AllocateIOZone(')


def io_header(source):
    text=once(source,'  MetadataWriter* metadata_writer_ = NULL;',
        '  uint8_t coloc_tag_=0;\n  std::set<uint64_t> coloc_zones_;\n'
        '  MetadataWriter* metadata_writer_ = NULL;')
    return once(text,'  std::string GetFilename();',
        '  std::string GetFilename();\n'
        '  uint8_t ColocationTag() {return coloc_tag_?coloc_tag_:zbd_->ColocationTag(GetFilename());}\n'
        '  const std::set<uint64_t>& ColocationZones() const {return coloc_zones_;}')


def io_source(source):
    text=once(source,'  kLinkedFilename = 9,','  kLinkedFilename = 9,\n  kColocationMembership = 10,')
    text=once(text,'\n}\n\nvoid ZoneFile::EncodeJson', '''
  // 独立原型只在确有协同成员时扩展元数据；普通K=0新盘编码保持原样。
  std::set<uint64_t> actual;
  for(const auto* ext:extents_)actual.insert(ext->zone_->start_);
  if(active_zone_)actual.insert(active_zone_->start_);
  std::set<uint64_t> membership;
  for(const auto zone:actual)
    if(coloc_zones_.count(zone) || (zbd_->ColocationPool() && zbd_->ColocationPool()->Contains(zone)))
      membership.insert(zone);
  coloc_zones_=membership;
  if(!membership.empty()) {
    coloc_tag_=ColocationTag();
    PutFixed32(output,kColocationMembership);PutFixed32(output,coloc_tag_);
    PutFixed32(output,membership.size());for(auto zone:membership)PutFixed64(output,zone);
  }
}

void ZoneFile::EncodeJson''')
    text=once(text,'      case kModificationTime:', '''      case kColocationMembership: {
        uint32_t kind=0,count=0;uint64_t zone=0;
        if(!GetFixed32(input,&kind) || !kind || kind>3 || !GetFixed32(input,&count) ||
            count>zbd_->GetNrZones())return Status::Corruption("协同池元数据头无效");
        coloc_tag_=kind;coloc_zones_.clear();
        for(uint32_t i=0;i<count;++i) {
          if(!GetFixed64(input,&zone) || !zbd_->GetIOZone(zone) ||
              zone%zbd_->GetZoneSize() || !coloc_zones_.insert(zone).second)
            return Status::Corruption("协同池元数据成员无效");
        }
        break;
      }
      case kModificationTime:''')
    text=once(text,'  is_sparse_ = update->IsSparse();\n  MetadataSynced();',
        '  is_sparse_ = update->IsSparse();\n'
        '  coloc_tag_=update->coloc_tag_;coloc_zones_=update->coloc_zones_;\n  MetadataSynced();')
    return once(text,'IOStatus ZoneFile::AllocateNewZone() {',
        'IOStatus ZoneFile::AllocateNewZone() {\n'
        '  rrzc_colocation::Scope colocation(ColocationTag());')


def fs_source(source):
    text=once(source,'  Info(logger_, "Superblock sequence %d",', '''  if(zbd_->ColocationPool()) {
    std::map<uint64_t,uint8_t> roles;
    std::lock_guard<std::mutex> lock(files_mtx_);
    for(const auto& item:files_) {
      const auto& file=item.second;
      for(const auto* ext:file->GetExtents())
        if(file->ColocationZones().count(ext->zone_->start_))roles[ext->zone_->start_]|=file->ColocationTag();
      if(file->HasActiveExtent()) {
        auto* z=zbd_->GetIOZone(file->GetExtentStart());
        if(z && file->ColocationZones().count(z->start_))roles[z->start_]|=file->ColocationTag();
      }
    }
    const auto restored=zbd_->RestoreColocation(roles);
    if(!restored.ok())return Status::Corruption(restored.ToString());
  }
  Info(logger_, "Superblock sequence %d",''')
    text=once(text,'    // 获取迁移目标 Zone。',
        '    // 明确排除本次全部源Zone；普通读取热点不会因此主动搬迁。\n'
        '    rrzc_colocation::Scope colocation(zfile->ColocationTag(),source_zones_for_colocation);\n'
        '    // 获取迁移目标 Zone。')
    return once(text,'  // 仅修改复制出的数据片段列表。',
        '  std::set<uint64_t> source_zones_for_colocation;\n'
        '  for(const auto* item:migrate_exts)source_zones_for_colocation.insert(item->zone_start);\n'
        '  // 仅修改复制出的数据片段列表。')
