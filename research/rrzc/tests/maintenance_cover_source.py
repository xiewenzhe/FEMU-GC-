"""仅在独立协同池副本接线；新开关默认关闭，原单位核和最终Greedy覆盖保留。"""
from calibration_source import once


def frontend(source):
    return once(source,'#include "rrzc_unit_cost_selector.h"',
        '#include "rrzc_unit_cost_selector.h"\n#include "rrzc_maintenance_cover.h"')


def selection(source):
    anchor='    if(unit_cost) {\n      const auto chosen=rrzc_unit_cost::Select(inputs);'
    replacement='''    const bool maintenance_cover=getenv("ZENFS_RRZC_MAINTENANCE_COVER") &&
        !strcmp(getenv("ZENFS_RRZC_MAINTENANCE_COVER"),"1");
    if(unit_cost && maintenance_cover) {
      const auto cover_need=rrzc_maintenance_cover::ExitNeed(need.free,need.non_free,
          need.waiters,unsigned(GC_START_LEVEL));
      const auto chosen=rrzc_maintenance_cover::Select(inputs,cover_need);
      Info(logger_,"空间覆盖需求：轮次=%lu，可知=%d，退出目标=%lu，退出缺口=%lu，分配等待=%lu，启动水平=%u",
          shadow_round,cover_need.known,cover_need.target,cover_need.bytes,cover_need.waiters,
          unsigned(GC_START_LEVEL));
      for(const auto& p:chosen.plans) {
        std::string members;
        for(auto z:p.zones){if(!members.empty())members+="/";members+=std::to_string(z);}
        Info(logger_,"空间覆盖候选：轮次=%lu，首区=%lu，覆盖=%d，当前计划复制=%s，计划外预计RR复制=%s，评分总复制=%s，静态回收=%s，计划外当前有效量=%s，计划区=%s",
            shadow_round,p.first,p.covered,rrzc_space_plan::Decimal(p.copy).c_str(),
            rrzc_space_plan::Decimal(p.pending_rr).c_str(),rrzc_space_plan::Decimal(p.Total()).c_str(),
            rrzc_space_plan::Decimal(p.reclaimed).c_str(),rrzc_space_plan::Decimal(p.deferred_valid).c_str(),
            members.c_str());
      }
      if(chosen.found)for(const auto& row:ranked)if(row.safe && row.candidate.zone==chosen.zone) {
        decision.found=true;decision.selected=row.candidate;break;
      }
      Info(logger_,"空间覆盖决策：轮次=%lu，存在=%d，Zone=%lu，输入非法=%d，仅空间进展=%d，实际只搬首区=1",
          shadow_round,chosen.found,chosen.found?chosen.zone:0,chosen.invalid,chosen.space_unknown);
    } else if(unit_cost) {
      const auto chosen=rrzc_unit_cost::Select(inputs);'''
    return once(source,anchor,replacement)
