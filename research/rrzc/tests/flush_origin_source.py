"""独立副本的来源捕获和元数据11；旧模式不写新记录，评分和RR/GC正文不改。"""
from calibration_source import once


def flush(source):
    if 'rrzc_flush_origin::Scope flush_origin' in source:raise ValueError('来源上下文已存在')
    source=once(source,'#include "db/flush_job.h"',
                '#include "db/flush_job.h"\n#include "rrzc_flush_origin.h"')
    return once(source,'      s = BuildTable(',
        '      // 上下文仅覆盖真实Flush的表创建，不以寿命提示数值代替来源。\n'
        '      rrzc_flush_origin::Scope flush_origin(rrzc_flush_origin::Enabled(),\n'
        '          static_cast<uint64_t>(job_context_->job_id));\n'
        '      s = BuildTable(')


def header(source):
    if 'flush_origin_w_' in source:raise ValueError('来源成员已存在')
    source=once(source,'  uint8_t coloc_tag_=0;',
        '  bool flush_origin_w_=false;\n  uint64_t flush_origin_job_=0;\n  uint8_t coloc_tag_=0;')
    return once(source,'  const std::set<uint64_t>& ColocationZones() const {return coloc_zones_;}',
        '  const std::set<uint64_t>& ColocationZones() const {return coloc_zones_;}\n'
        '  void MaybeCaptureFlushOrigin();\n'
        '  bool HasFlushOrigin() const {return flush_origin_w_;}\n'
        '  uint64_t FlushOriginJob() const {return flush_origin_job_;}')


def io(source):
    if 'kFlushOrigin = 11' in source:raise ValueError('来源编码已存在')
    source=once(source,'#include "io_zenfs.h"','#include "io_zenfs.h"\n#include "rrzc_flush_origin.h"\n#include <cstdio>')
    source=once(source,'  kColocationMembership = 10,','  kColocationMembership = 10,\n  kFlushOrigin = 11,')
    source=once(source,'  if(!membership.empty()) {','  if(!membership.empty() || flush_origin_w_) {')
    anchor='    PutFixed32(output,membership.size());for(auto zone:membership)PutFixed64(output,zone);\n  }'
    source=once(source,anchor,anchor+'''
  // 新来源文件即使普通回退也保留标签；普通旧文件不产生记录11。
  if(flush_origin_w_) {
    PutFixed32(output,kFlushOrigin);PutFixed32(output,1);
    PutFixed64(output,flush_origin_job_);
  }''')
    source=once(source,'      case kModificationTime:', '''      case kFlushOrigin: {
        uint32_t version=0;uint64_t job=0;
        if(flush_origin_w_ || coloc_tag_!=2 || !GetFixed32(input,&version) || version!=1 ||
            !GetFixed64(input,&job))return Status::Corruption("Flush来源元数据损坏或顺序非法");
        flush_origin_w_=true;flush_origin_job_=job;break;
      }
      case kModificationTime:''')
    source=once(source,'  coloc_tag_=update->coloc_tag_;coloc_zones_=update->coloc_zones_;',
        '  coloc_tag_=update->coloc_tag_;coloc_zones_=update->coloc_zones_;\n'
        '  flush_origin_w_=update->flush_origin_w_;flush_origin_job_=update->flush_origin_job_;')
    source=once(source,'IOStatus ZoneFile::AllocateNewZone() {','''void ZoneFile::MaybeCaptureFlushOrigin() {
  if(flush_origin_w_ || !zbd_->ColocationPool() || !rrzc_flush_origin::current.active || ColocationTag())return;
  const std::string name=GetFilename();
  if(name.size()<4 || name.substr(name.size()-4)!=".sst")return;
  coloc_tag_=2;flush_origin_w_=true;flush_origin_job_=rrzc_flush_origin::current.job;
  std::fprintf(stderr,"Flush来源W标记：文件编号=%lu，文件名=%s，任务=%lu，标签=2，未来寿命未知=1\\n",
      file_id_,name.c_str(),flush_origin_job_);
}

IOStatus ZoneFile::AllocateNewZone() {
  MaybeCaptureFlushOrigin();''')
    return source


def fs(source):
    if 'Flush来源挂载文件：' in source:raise ValueError('来源恢复诊断已存在')
    marker='      const auto& file=item.second;\n      for(const auto* ext:file->GetExtents())'
    return once(source,marker,'''      const auto& file=item.second;
      if(file->HasFlushOrigin())
        Info(logger_,"Flush来源挂载文件：文件编号=%lu，文件名=%s，任务=%lu，标签=%u，成员数=%zu",
            file->GetID(),file->GetFilename().c_str(),file->FlushOriginJob(),
            static_cast<unsigned>(file->ColocationTag()),file->ColocationZones().size());
      for(const auto* ext:file->GetExtents())''')


def business_wrapper(source):
    source=once(source,'run_bench() {','flush_w=0\nrun_bench() {')
    source=once(source,'ZENFS_RRZC_POOL_K=1','ZENFS_RRZC_POOL_FLUSH_W="$flush_w" ZENFS_RRZC_POOL_K=1')
    source=once(source,'hotspot=1;phase run','flush_w=1;hotspot=1;phase run')
    return once(source,'hotspot=0;phase verify','flush_w=0;hotspot=0;phase verify')
