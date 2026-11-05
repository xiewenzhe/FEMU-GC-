"""在已完成Flush来源的独立副本接线；原档案、元数据格式和选区正文不改。"""
from calibration_source import once


def zbd_header(text):
    if 'pair_admission_' in text:raise ValueError('配对准入成员已存在')
    text=once(text,'#pragma once','#pragma once\n#include "rrzc_pair_admission.h"\n#include "rrzc_all_blocks_io.h"')
    return once(text,'  void InitColocationPool();','''  void InitColocationPool();
  bool pair_admission_=false;
  bool pair_observe_=false;
  uint64_t pair_copy_budget_=0;
  std::atomic<uint64_t> pair_sequence_{0};
  rrzc_pair::Ledger pair_ledger_;
  void InitPairAdmission();
  bool AdmitColocation(Zone*,uint32_t,bool);
 public:
  bool PairAdmissionObserved() const {return pair_observe_;}
  rrzc_pair::Ledger& PairLedger() {return pair_ledger_;}
 private:''')


def adapter(text):
    if 'AdmitColocation' in text:raise ValueError('配对准入入口已存在')
    text=once(text,'void ZonedBlockDevice::InitColocationPool() {',
        '#include "rrzc_pair_zenfs.inc"\n\nvoid ZonedBlockDevice::InitColocationPool() {\n  InitPairAdmission();')
    # 已取得目标独占且容量/代际重核验通过，尚未取得Active令牌或提交成员。
    return once(text,'  bool active=false,opened=false;',
        '  if(!AdmitColocation(z,required,own_open))return z->CheckRelease();\n'
        '  bool active=false,opened=false;')


def io_header(text):
    if 'PublishPairExtents' in text:raise ValueError('有效量发布成员已存在')
    return once(text,'  void MaybeCaptureFlushOrigin();',
        '  void MaybeCaptureFlushOrigin();\n  void PublishPairExtents();')


def io(text):
    if 'PublishPairExtents' in text:raise ValueError('有效量发布入口已存在')
    text=once(text,'ZoneFile::~ZoneFile() { ClearExtents(); }','''void ZoneFile::PublishPairExtents() {
  if(!zbd_->PairAdmissionObserved())return;
  std::map<uint64_t,uint64_t> actual;
  for(const auto* ext:extents_)actual[ext->zone_->start_]+=ext->length_;
  // 只统计原生used_capacity已经确认的extent；写入中尚未提交的字节不伪称有效。
  const uint8_t tag=linkfiles_.empty()?coloc_tag_:ColocationTag();
  zbd_->PairLedger().Publish(this,tag,actual);
}

ZoneFile::~ZoneFile() { ClearExtents(); }''')
    text=once(text,'  extents_.clear();','  extents_.clear();\n  PublishPairExtents();')
    # 完整解码后才有文件名/标签；临时恢复对象也计入，与原生used_capacity口径一致。
    text=once(text,'  MetadataSynced();\n  return Status::OK();',
        '  MetadataSynced();\n  PublishPairExtents();\n  return Status::OK();')
    text=once(text,'  for (const auto& name : update->GetLinkFiles()) linkfiles_.push_back(name);',
        '  for (const auto& name : update->GetLinkFiles()) linkfiles_.push_back(name);\n  PublishPairExtents();')
    for anchor in ('  active_zone_->used_capacity_ += length;',
                   '    active_zone_->used_capacity_ += extent_length;'):
        count=text.count(anchor)
        expected=2 if anchor=='    active_zone_->used_capacity_ += extent_length;' else 1
        if count!=expected:raise ValueError('有效量更新锚点数量改变：'+anchor)
        text=text.replace(anchor,anchor+'\n'+anchor[:len(anchor)-len(anchor.lstrip())]+'PublishPairExtents();')
    # 恢复路径原生代码先加used，再插入extent；必须等插入后才发布。
    for anchor in ('    extents_.push_back(new ZoneExtent(next_extent_start + SPARSE_HEADER_SIZE,\n'
                   '                                      extent_length, zone));',
                   '    extents_.push_back(new ZoneExtent(extent_start_, to_recover, zone));'):
        text=once(text,anchor,anchor+'\n    PublishPairExtents();')
    return text


def fs(text):
    if 'PublishPairExtents' in text:raise ValueError('有效映射发布已存在')
    # 成功映射与旧used_capacity同步完成后发布；不在复制尚未提交时伪造新有效映射。
    return once(text,'    delete old_ext;\n  }\n\n  return IOStatus::OK();',
        '    delete old_ext;\n  }\n  zoneFile->PublishPairExtents();\n\n  return IOStatus::OK();')


def business_wrapper(text):
    # 两臂仅该业务开关不同，装载/准备/验证仍关闭；同一新程序、同一采证开销。
    text=once(text,'flush_w=0\nrun_bench() {','pair_on=0\nflush_w=0\nrun_bench() {')
    text=once(text,'ZENFS_RRZC_POOL_FLUSH_W="$flush_w"',
        'ZENFS_RRZC_POOL_PAIR_ADMISSION="$pair_on" ZENFS_RRZC_POOL_PAIR_OBSERVE=1 ZENFS_RRZC_POOL_PAIR_COPY_BUDGET=0 '
        'ZENFS_RRZC_POOL_FLUSH_W="$flush_w"')
    text=once(text,'flush_w=1;hotspot=1;phase run',
        'pair_on="${RRZC_PAIR_ARM:-0}";flush_w=1;hotspot=1;phase run')
    return once(text,'flush_w=0;hotspot=0;phase verify','pair_on=0;flush_w=0;hotspot=0;phase verify')


def arm_wrapper(value):
    if value not in (0,1):raise ValueError('配对臂开关非法')
    return ('#!/usr/bin/env bash\n# 仅切换本臂准入；原业务脚本负责设备边界及退出采证。\n'
        'set -euo pipefail\nexport RRZC_PAIR_ARM='+str(value)+'\n'
        'exec bash "$(dirname "$(readlink -f "$0")")/run_pool_business_guest.sh" "$@"\n')
