"""仅在已接入账目的独立ZenFS副本增加双目标比较；旧配对模式保持独立。"""
from calibration_source import once


def header(text):
    if 'target_compare_' in text:raise ValueError('双目标成员已存在')
    text=once(text,'#include "rrzc_pair_admission.h"',
              '#include "rrzc_pair_admission.h"\n#include "rrzc_target_compare.h"')
    return once(text,'  void InitPairAdmission();','''  void InitPairAdmission();
  bool target_compare_=false,target_observe_=false;
  std::atomic<uint64_t> target_sequence_{0};
  void InitTargetCompare();
  IOStatus CompareMigrationTargets(Zone*,uint32_t,Env::WriteLifeTimeHint,Zone**);''')


def adapter(text):
    if 'CompareMigrationTargets(z' in text:raise ValueError('双目标入口已存在')
    text=once(text,'#include "rrzc_pair_zenfs.inc"',
              '#include "rrzc_pair_zenfs.inc"\n#include "rrzc_target_compare_zenfs.inc"')
    text=once(text,'  InitPairAdmission();','  InitPairAdmission();\n  InitTargetCompare();')
    return once(text,'  if(!AdmitColocation(z,required,own_open))return z->CheckRelease();','''  if(target_observe_ && !own_open &&
      (static_cast<uint8_t>(rrzc_colocation::current.tag)&1)) {
    Zone* alternative=nullptr;
    const auto compared=CompareMigrationTargets(z,required,lifetime,&alternative);
    if(!compared.ok()) {
      const auto released=z->CheckRelease();
      return released.ok()?compared:released;
    }
    if(alternative) {
      const auto released=z->CheckRelease();
      if(!released.ok()) {
        const auto other=alternative->CheckRelease();
        return other.ok()?released:other;
      }
      *output=alternative;return IOStatus::OK();
    }
  }
  if(!AdmitColocation(z,required,own_open))return z->CheckRelease();''')


def business_wrapper(text):
    text=once(text,'pair_on=0\nflush_w=0', 'target_on=0\npair_on=0\nflush_w=0')
    text=once(text,'ZENFS_RRZC_POOL_PAIR_ADMISSION="$pair_on"',
        'ZENFS_RRZC_POOL_TARGET_COMPARE="$target_on" ZENFS_RRZC_POOL_TARGET_OBSERVE=1 '
        'ZENFS_RRZC_POOL_PAIR_ADMISSION="$pair_on"')
    text=once(text,'pair_on="${RRZC_PAIR_ARM:-0}";flush_w=1;hotspot=1;phase run',
        'target_on="${RRZC_TARGET_ARM:-0}";pair_on=0;flush_w=1;hotspot=1;phase run')
    return once(text,'pair_on=0;flush_w=0;hotspot=0;phase verify',
        'target_on=0;pair_on=0;flush_w=0;hotspot=0;phase verify')


def arm_wrapper(value):
    if value not in (0,1):raise ValueError('双目标臂开关非法')
    return ('#!/usr/bin/env bash\n# 两臂共同采证，仅切双目标执行，不启用旧配对拒绝。\n'
        'set -euo pipefail\nexport RRZC_TARGET_ARM='+str(value)+'\n'
        'exec bash "$(dirname "$(readlink -f "$0")")/run_pool_business_guest.sh" "$@"\n')
