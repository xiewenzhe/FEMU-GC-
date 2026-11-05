"""只在独立ZenFS副本增加首区模型；关闭开关后保留冻结评分路径。"""
from calibration_source import once


def snapshot(text):
    text=once(text,'  uint64_t file_id;','  bool first_action_hot=false;\n  uint64_t file_id;')
    return once(text,'        lifetime_hint(file.GetWriteLifeTimeHint()) {',
        '        lifetime_hint(file.GetWriteLifeTimeHint()) {\n    first_action_hot=(file.ColocationTag() & 1)!=0;')


def fs(text):
    text=once(text,'#include "rrzc_all_blocks_io.h"',
        '#include "rrzc_all_blocks_io.h"\n#include "rrzc_first_action.h"')
    text=once(text,'\n  while (run_gc_worker_) {',
        '''
  const char* first_env=getenv("ZENFS_RRZC_FIRST_ACTION");
  if(first_env && strcmp(first_env,"0") && strcmp(first_env,"1"))
    throw std::invalid_argument("首区模型开关只能为0或1");
  const bool first_action=first_env && !strcmp(first_env,"1");
  rrzc_first_action::Files first_files;
  uint64_t first_file_time=0;
  Info(logger_,"首区模型配置：版本=1，启用=%d，存活最少秒=2，折扣尺度上限秒=30，折扣非概率=1，原资格不变=1",first_action);
  while (run_gc_worker_) {''')
    # 每个原维护检查点观察一次文件，不新发GC/RR请求；两臂共同启用。
    return once(text,'    if (free_percent > GC_START_LEVEL) continue;',
        '''    if(first_action) {
      ZenFSSnapshot observed;ZenFSSnapshotOptions file_options;file_options.zone_file_=1;
      GetZenFSSnapshot(observed,file_options);
      first_file_time=std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now().time_since_epoch()).count();
      std::vector<rrzc_first_action::File> files;
      for(const auto& f:observed.zone_files_) {
        uint64_t bytes=0;for(const auto& ext:f.extents)bytes+=ext.length;
        files.push_back({f.file_id,bytes,f.first_action_hot});
      }
      first_files.Observe(first_file_time,files);
    }
    if (free_percent > GC_START_LEVEL) continue;''')


def direct(text):
    anchor='    if(unit_cost && maintenance_cover) {'
    branch='''    if(first_action) {
      std::vector<rrzc_first_action::Input> actions;
      for(const auto& row:ranked) {
        const auto& c=row.candidate;
        const auto maximum=usable.find(c.zone), unused=usable.end();
        const auto frame=all_blocks_current.find(c.zone);
        const auto past=all_blocks_frozen.find(c.zone);
        const rrzc_all_blocks::Report* report=nullptr;
        if(maximum!=unused && frame!=all_blocks_current.end() && !frame->second.blocks.empty()) {
          const auto& block=frame->second.blocks.front();const auto& b=maximum->second;
          if(block.generation==b.generation && block.epoch_start_ns==b.epoch_start_ns &&
              block.snapshot_ns>=b.snapshot_ns && block.snapshot_ns-b.snapshot_ns<=2*rrzc_v1::kSecond)
            report=&frame->second;
        }
        const auto risk=rrzc_first_action::Predict(c,report,past==all_blocks_frozen.end()?nullptr:&past->second);
        uint64_t credit=0,hot=0,covered=0;
        for(const auto& file:snapshot.zone_files_) {
          uint64_t bytes=0,total=0;
          for(const auto& ext:file.extents){total+=ext.length;if(ext.zone_start==c.zone)bytes+=ext.length;}
          if(!bytes)continue;
          covered+=bytes;
          const auto span=first_files.Span(file.file_id,total,first_file_time);
          const auto amount=file.first_action_hot?rrzc_first_action::Credit(bytes,span,risk.eta):0;
          if(file.first_action_hot)hot+=bytes;
          credit+=amount;
          Info(logger_,"首区文件：轮次=%lu，Zone=%lu，文件=%lu，区内字节=%lu，总字节=%lu，热读=%d，存活纳秒=%lu，抵扣=%lu",
              shadow_round,c.zone,file.file_id,bytes,total,file.first_action_hot,span,amount);
        }
        const bool safe=row.safe && covered==c.valid && credit<=c.valid;
        if(!risk.known)credit=0;
        actions.push_back({c,safe,risk,credit});
        Info(logger_,"首区候选：轮次=%lu，Zone=%lu，有效=%lu，回收=%lu，安全=%d，风险可知=%d，块=%u，ETA=%lu，热字节=%lu，覆盖=%lu，抵扣=%lu，净复制=%lu，代际=%lu，RC=%lu，近期增量=%lu，近期间隔=%lu，十秒增量=%lu，代际年龄=%lu，阈值=%lu",
            shadow_round,c.zone,c.valid,c.capacity-c.valid,safe,risk.known,risk.block,risk.eta,hot,covered,credit,c.valid-credit,
            risk.generation,risk.rc,risk.delta,risk.interval,risk.delta10,risk.age,recent.threshold);
      }
      const auto chosen=rrzc_first_action::Select(actions);
      if(chosen.found)for(const auto& row:ranked)if(row.safe && row.candidate.zone==chosen.zone) {
        decision.found=true;decision.selected=row.candidate;break;
      }
      Info(logger_,"首区建议：轮次=%lu，存在=%d，Zone=%lu，补区计划=0",shadow_round,chosen.found,chosen.found?chosen.zone:0);
    } else if(unit_cost && maintenance_cover) {'''
    return once(text,anchor,branch)


def apply(target):
    folder=target/'协同池副本/plugin/zenfs/fs'
    for name,fn in [('snapshot.h',snapshot),('fs_zenfs.cc',fs),('rrzc_direct_zenfs.inc',direct)]:
        p=folder/name;p.write_text(fn(p.read_text()))
    __import__('shutil').copyfile(target/'rrzc_first_action.h',folder/'rrzc_first_action.h')


def wrapper(text):
    text=once(text,'if [[ $arm_tag == m* ]]; then tail_on=1; fi','tail_on=0')
    return once(text,'ZENFS_RRZC_POOL_TAIL_OBSERVE=1',
        'ZENFS_RRZC_FIRST_ACTION=1 ZENFS_RRZC_POOL_TAIL_OBSERVE=1')
