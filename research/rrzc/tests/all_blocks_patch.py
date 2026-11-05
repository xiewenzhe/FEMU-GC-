#!/usr/bin/env python3
"""针对已部署源码生成默认关闭的全块风险接线补丁；拒绝模糊替换。"""
import difflib
from pathlib import Path

def once(text,old,new):
    if text.count(old)!=1:raise ValueError('接线锚点不是唯一匹配：'+old[:100])
    return text.replace(old,new,1)

def sources(fs,inc):
    fs=once(fs,'#include "rrzc_joint_time_selector.h"',
        '#include "rrzc_joint_time_selector.h"\n#include "rrzc_all_blocks_io.h"')
    key='  rrzc_deadline::History deadline_frozen;'
    fs=once(fs,key,key+'''
  const char* all_blocks_env=getenv("ZENFS_RRZC_ALL_BLOCKS");
  const bool all_blocks_enabled=all_blocks_env && !strcmp(all_blocks_env,"1");
  rrzc_all_blocks::Store all_blocks_history;
  rrzc_all_blocks::Store::Histories all_blocks_frozen;
  auto read_all_blocks=[&](const rrzc_v1::RecentReport& maximum) {
    std::map<uint64_t,rrzc_all_blocks::Report> reports;
    for(const auto& b:maximum.zones) {
      Zone* zone=zbd_->GetIOZone(b.zone);
      if(!zone || !zone->IsFull())continue;
      rrzc_all_blocks::Report report;std::string error;
      if(rrzc_all_blocks::QueryPath(zbd_->GetFilename(),zbd_->GetNrZones(),
          maximum.blocks_per_zone,b.zone_index,zbd_->GetZoneSize(),&report,&error) &&
          report.threshold==maximum.threshold && report.session_hi==maximum.session_hi &&
          report.session_lo==maximum.session_lo)reports.emplace(b.zone,std::move(report));
      else Info(logger_,"全块查询失败：Zone=%lu，原因=%s",b.zone,error.c_str());
    }
    return reports;
  };''')
    key='  const bool joint_time_enabled=joint_time_env && !strcmp(joint_time_env,"1");'
    fs=once(fs,key,key+'''
  if(all_blocks_enabled && (!joint_time_enabled ||
      !getenv("ZENFS_RRZC_RECENT_SAMPLER") || strcmp(getenv("ZENFS_RRZC_RECENT_SAMPLER"),"1"))) {
    Error(logger_,"全块风险配置冲突：必须启用同窗成本与独立采样");return;
  }
  Info(logger_,"全块风险配置：启用=%d，版本=1，按最早同块ETA选择风险代表，整区RR只计一次，旧C2不变=1",all_blocks_enabled);''')
    key='    if (deadline_enabled) deadline_history.Publish(*sample,good);'
    fs=once(fs,key,key+'''
    if(all_blocks_enabled) {
      std::vector<rrzc_all_blocks::Report> reports;
      if(good)for(auto& item:read_all_blocks(*sample))reports.push_back(std::move(item.second));
      all_blocks_history.Publish(reports);
    }''')
    key='        const auto query_start=std::chrono::steady_clock::now();'
    fs=once(fs,key,'''        if(all_blocks_enabled)all_blocks_frozen=all_blocks_history.Freeze();
'''+key)
    key='        const IOStatus recent_status=zbd_->GetZoneRecentReads(&recent);'
    fs=once(fs,key,key+'''
        const auto all_blocks_current=all_blocks_enabled && recent_status.ok() ?
            read_all_blocks(recent) : std::map<uint64_t,rrzc_all_blocks::Report>{};''')
    key='      const auto cost=joint_time_enabled ? rrzc_joint_time::Estimate(c,b==usable.end()?nullptr:&b->second,'
    inc=once(inc,key,key.replace('const auto cost','auto cost'))
    key='      if(joint_time_enabled) {\n        e=joint.base;'
    inc=once(inc,key,'''      if(all_blocks_enabled) {
        const auto found=all_blocks_current.find(c.zone);
        const auto history=all_blocks_frozen.find(c.zone);
        const rrzc_all_blocks::Report* report=nullptr;
        if(b!=usable.end() && found!=all_blocks_current.end()) {
          const auto& frame=found->second;const auto& first=frame.blocks.front();
          // C4位于C2之后，源稳定检查包围两次查询；不能跨Reset拼接。
          if(first.generation==b->second.generation && first.epoch_start_ns==b->second.epoch_start_ns &&
              first.snapshot_ns>=b->second.snapshot_ns &&
              first.snapshot_ns-b->second.snapshot_ns<=2*rrzc_v1::kSecond)report=&frame;
        }
        rrzc_all_blocks::Evidence proof;
        cost=rrzc_all_blocks::Estimate(c,report,history==all_blocks_frozen.end()?nullptr:&history->second,
            f==window_forecasts.end()?nullptr:&f->second,shadow_horizon,&proof);
        joint=proof.joint;
        for(const auto& item:proof.distribution) {
          const auto& x=item.block;const auto& g=item.growth;
          Info(logger_,"全块风险明细：轮次=%lu，Zone=%lu，会话=%lu/%lu，代际=%lu，块=%u，PPA=%u/%u/%u/%u，快照=%lu，代际起点=%lu，RC=%lu，十秒增量=%lu，三十秒增量=%lu，标志=%u，前快照=%lu，前RC=%lu，近期可知=%d，近期增量=%lu，近期间隔=%lu，风险可知=%d，ETA=%lu",
              shadow_round,c.zone,report->session_hi,report->session_lo,x.generation,x.block_id,
              x.ch,x.lun,x.plane,x.block,x.snapshot_ns,x.epoch_start_ns,x.rc,x.delta10,x.delta30,x.flags,
              g.previous.snapshot_ns,g.previous.rc,g.known,g.delta,g.interval_ns,item.cost.risk_known,item.cost.eta);
        }
        Info(logger_,"全块风险代表：轮次=%lu，Zone=%lu，完整=%d，观察块数=%u，可知块数=%u，存在=%d，最大RC块=%u，代表块=%u，代表RC=%lu，ETA=%lu，RR抵扣=%lu",
            shadow_round,c.zone,proof.complete,proof.observed,proof.known,proof.selected,
            b==usable.end()?0:b->second.block_id,proof.block.block_id,proof.block.rc,cost.eta,cost.credit);
      }
'''+key)
    return fs,inc

def make_patch(fs,inc):
    changed=sources(fs,inc);patch=''
    for name,old,new in zip(('fs_zenfs.cc','rrzc_direct_zenfs.inc'),(fs,inc),changed):
        patch+=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),
            fromfile='a/fs/'+name,tofile='b/fs/'+name))
    return patch

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();patch=make_patch((a.source/'fs_zenfs.cc').read_text(),(a.source/'rrzc_direct_zenfs.inc').read_text())
    with a.output.open('x') as f:f.write(patch)
