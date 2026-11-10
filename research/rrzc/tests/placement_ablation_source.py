"""仅修改独立ZenFS副本；模式接线可逆，原安全检查及目标选择正文逐字保留。"""
from calibration_source import once

INCLUDE='#include "rrzc_placement_mode.h"\n'
MEMBER='  rrzc_placement::Mode placement_mode_=rrzc_placement::Mode::kLegacy;\n'
INIT='''  const auto& placement=rrzc_placement::ProcessSettings();
  placement_mode_=placement.mode;
  if(placement.Explicit())
    Info(logger_,"放置消融配置：模式=%s，K=%d，H统计=1，Flush来源=1，H迁移入池=%d，进程内不切换=1",
         rrzc_placement::Name(placement_mode_),placement_mode_==rrzc_placement::Mode::kG0?0:1,
         placement_mode_==rrzc_placement::Mode::kG2);
  const char* k=placement.PoolK();'''
GATE='''  // G1仅跳过必要迁移中的H/HW协同目标；不抹掉真实标签，不取得任何令牌。
  if(rrzc_placement::SkipHotMigration(placement_mode_,own_open,
         static_cast<uint8_t>(rrzc_colocation::current.tag))) {
    Info(logger_,"放置消融H迁移回退：模式=G1，类别=%u，协同目标=未评估，继续普通分配=1",
         static_cast<unsigned>(rrzc_colocation::current.tag));
    return IOStatus::OK();
  }
'''


def header(text):
    return once(text,'#pragma once','#pragma once\n'+INCLUDE)


def zbd_header(text):
    text=header(text)
    return once(text,'  std::unique_ptr<rrzc_colocation::Pool> coloc_pool_;',
        MEMBER+'  std::unique_ptr<rrzc_colocation::Pool> coloc_pool_;')


def adapter(text):
    text=once(text,'  const char* k=getenv("ZENFS_RRZC_POOL_K");',INIT)
    return once(text,'  *output=nullptr;\n', '  *output=nullptr;\n'+GATE)


def undo_adapter(text):
    text=once(text,INIT,'  const char* k=getenv("ZENFS_RRZC_POOL_K");')
    return once(text,GATE,'')


def stream_header(text):
    text=header(text)
    return once(text,'  static const bool enabled=[] {',
        '  static const bool enabled=[] {\n'
        '    if(rrzc_placement::ProcessSettings().Explicit())return true;')


def flush_header(text):
    text=header(text)
    return once(text,'inline bool Enabled() {',
        'inline bool Enabled() {\n'
        '  if(rrzc_placement::ProcessSettings().Explicit())return true;')


TRANSFORMS={'zbd_zenfs.h':zbd_header, 'rrzc_colocation_zenfs.inc':adapter,
    'rrzc_stream_profile.h':stream_header, 'rrzc_flush_origin.h':flush_header}
