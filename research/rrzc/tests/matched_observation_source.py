"""独立诊断对象统一风险计算与日志；最终执行仍分别遵循Greedy或原模型。"""
from calibration_source import once


def direct(source):
    text=once(source,'  std::map<uint64_t,rrzc_retention::Forecast> forecasts;',
        '  const bool cal_common=getenv("RRZC_CAL_COMMON_OBSERVATION") &&\n'
        '      !strcmp(getenv("RRZC_CAL_COMMON_OBSERVATION"),"1");\n'
        '  std::map<uint64_t,rrzc_retention::Forecast> forecasts;')
    text=once(text,'        for(const auto& item:proof.distribution) {',
        '        // 只关闭额外逐块诊断输出；风险分布与成本计算原文保留。\n'
        '        if(!cal_common)for(const auto& item:proof.distribution) {')
    anchor='    Info(logger_,"时序方案决策：轮次=%lu，实际存在=%d，实际Zone=%lu，仅进展=%d",'
    added='''    // 两臂先执行相同的查询及模型计算；只在最后执行点区分策略。
    if(cal_common) {
      const uint64_t proposed=decision.found?decision.selected.zone:UINT64_MAX;
      const auto greedy_rows=rrzc_direct::Rank(candidates,{}, {},recent.threshold,simple_budget,shadow_horizon);
      rrzc_simple::Decision cal_greedy{};
      for(const auto& preferred:greedy_rows) {
        const auto shared=std::find_if(ranked.begin(),ranked.end(),[&](const auto& row) {
          return row.candidate.zone==preferred.candidate.zone && row.safe;
        });
        if(shared!=ranked.end()) {cal_greedy.found=true;cal_greedy.selected=shared->candidate;break;}
      }
      const char* execute=getenv("RRZC_CAL_GREEDY_EXECUTE");
      const bool greedy_execute=execute && !strcmp(execute,"1");
      if(greedy_execute)decision=cal_greedy;
      Info(logger_,"共同观测实际决策：轮次=%lu，执行Greedy=%d，模型存在=%d，模型Zone=%lu，Greedy存在=%d，GreedyZone=%lu，实际存在=%d，实际Zone=%lu，逐块明细关闭=1",
          shadow_round,greedy_execute,proposed!=UINT64_MAX,proposed,cal_greedy.found,
          cal_greedy.found?cal_greedy.selected.zone:0,decision.found,decision.found?decision.selected.zone:0);
    }
'''
    return once(text,anchor,added+anchor)


def backend(source):
    text='#include "rrzc_io_trace.h"\n'+source
    return once(text,'      const int status=ioctl(read_f_,NVME_IOCTL_IO_CMD,&cmd);',
        '      const int status=ioctl(read_f_,NVME_IOCTL_IO_CMD,&cmd);\n'
        '      rrzc_io_trace::Read(lba,rrzc_strict::migration,status,cmd.result);')


def frontend(source):
    text=once(source,'#pragma once','#pragma once\n#include "rrzc_io_trace.h"')
    text=once(text,'    if(trace_) {trace_index_=index;trace_key_=key;trace_begin_=Now();trace_api_=0;}',
        '    if(trace_) {trace_index_=index;trace_key_=key;trace_begin_=Now();trace_api_=0;}\n'
        '    rrzc_io_trace::Begin(operations_-1,writer,index,key);')
    text=once(text,'    busy_=false;','    rrzc_io_trace::End(operations_-1);\n    busy_=false;')
    return once(text,'  void FinishTrace() {','  void FinishTrace() {\n    rrzc_io_trace::Finish();')


def wrapper(source):
    anchor='enabled=0;greedy=1;planning=()'
    text=once(source,anchor,'cal_greedy_execute=1\n'+anchor)
    old='''if [[ $mode == blocks ]]; then
  enabled=1;greedy=0
  planning=(ZENFS_RRZC_SPACE_PLAN=1 ZENFS_RRZC_DEADLINE=1 ZENFS_RRZC_JOINT_TIME=1 ZENFS_RRZC_TERMINAL=0)
fi'''
    new='''if [[ $mode == blocks ]]; then cal_greedy_execute=0; fi
# 查询、历史及模型建议两臂都启用；最终执行点另按原Greedy或原模型选择。
enabled=1;greedy=0
planning=(ZENFS_RRZC_SPACE_PLAN=1 ZENFS_RRZC_DEADLINE=1 ZENFS_RRZC_JOINT_TIME=1 ZENFS_RRZC_TERMINAL=0)'''
    text=once(text,old,new)
    return once(text,'ZENFS_MAINT_DIAG=1',
        'RRZC_CAL_COMMON_OBSERVATION=1 RRZC_CAL_GREEDY_EXECUTE="$cal_greedy_execute" '
        'RRZC_CAL_IO_TRACE="${request_trace:+$result/请求设备摘要.bin}" ZENFS_MAINT_DIAG=1')
