"""为独立客机的ZenFS对象增加仅实验启用的批次检查门；原GC判断与选区源码逐字保留。"""
from calibration_source import once


def patched(source):
    anchor='  while (run_gc_worker_) {\n    if (!growth_enabled || independent_sampling) {'
    replacement='''  // 受控重复实验将后台检查对齐到同一前台批次；资格、候选和RR阈值不变。
  const char* gc_gate=getenv("ZENFS_RRZC_CAL_GC_GATE");
  const bool batch_gate=gc_gate && gc_gate[0];
  uint64_t last_gate=0,active_gate=0;
  auto next_emergency=std::chrono::steady_clock::now();
  struct GateAck {
    const char* path; uint64_t sequence;
    ~GateAck() {
      if (!path) return;
      const std::string temporary=std::string(path)+".ack.tmp."+std::to_string(getpid());
      FILE* stream=fopen(temporary.c_str(),"w");
      if (!stream) return;
      const int written=fprintf(stream,"%llu\\n",(unsigned long long)sequence);
      const int closed=fclose(stream);
      if (written<0 || closed!=0) return;
      rename(temporary.c_str(),(std::string(path)+".ack").c_str());
    }
  };
  if (batch_gate) Info(logger_,"受控GC批次检查：启用=1，原启动水平=%lu%%，仅改变检查时点",GC_START_LEVEL);
  while (run_gc_worker_) {
    if (batch_gate) {
      while (run_gc_worker_) {
        FILE* request=fopen((std::string(gc_gate)+".request").c_str(),"r");
        unsigned long long value=0;
        const bool readable=request && fscanf(request,"%llu",&value)==1;
        if (request) fclose(request);
        if (readable && value>last_gate) {
          active_gate=value;last_gate=value;
          Info(logger_,"受控GC检查点开始：批次=%lu",active_gate);
          break;
        }
        // 真正等待空Zone的前台无法完成批次；每秒最多唤醒一次原GC资格检查。
        const auto now=std::chrono::steady_clock::now();
        const uint64_t waiters=zbd_->GetAllocationWaiters();
        if (waiters && now>=next_emergency) {
          active_gate=0;
          next_emergency=now+std::chrono::seconds(1);
          Info(logger_,"受控GC紧急检查：上一完成批次=%lu，真实分配等待数=%lu",last_gate,waiters);
          break;
        }
        usleep(1000);
      }
    } else if (!growth_enabled || independent_sampling) {'''
    text=once(source,anchor,replacement)
    text=once(text,'    if (!run_gc_worker_) break;\n    auto strict_lock=zbd_->StrictLock();',
        '    if (!run_gc_worker_) break;\n    GateAck gate_ack{batch_gate && active_gate?gc_gate:nullptr,active_gate};\n'
        '    auto strict_lock=zbd_->StrictLock();')
    # 以下整个GC触发、选区、迁移判断原文未改；析构确认覆盖所有continue分支。
    return text


def synchronous_space(source):
    """用请求/确认替换紧急计时轮询；整轮检查结束后才唤醒分配者。"""
    text=once(source,'#include "fs_zenfs.h"','#include "fs_zenfs.h"\n#include "rrzc_boundary_sync.h"')
    text=once(text,'  auto next_emergency=std::chrono::steady_clock::now();','  uint64_t active_pressure=0;')
    old='''        // 真正等待空Zone的前台无法完成批次；每秒最多唤醒一次原GC资格检查。
        const auto now=std::chrono::steady_clock::now();
        const uint64_t waiters=zbd_->GetAllocationWaiters();
        if (waiters && now>=next_emergency) {
          active_gate=0;
          next_emergency=now+std::chrono::seconds(1);
          Info(logger_,"受控GC紧急检查：上一完成批次=%lu，真实分配等待数=%lu",last_gate,waiters);
          break;
        }'''
    new='''        // 只响应原重试确实失败后的明确请求，不把瞬时fallback登记当维护需求。
        const uint64_t pressure=rrzc_boundary::State().Pending();
        if (pressure) {
          active_gate=0;active_pressure=pressure;
          Info(logger_,"受控GC紧急检查：上一完成批次=%lu，真实分配等待数=%lu，空间请求=%lu",
              last_gate,zbd_->GetAllocationWaiters(),pressure);
          break;
        }'''
    text=once(text,old,new)
    text=once(text,'    if (batch_gate) {\n      while (run_gc_worker_) {',
        '    if (batch_gate) {\n      active_pressure=0;\n      while (run_gc_worker_) {')
    text=once(text,'    GateAck gate_ack{batch_gate && active_gate?gc_gate:nullptr,active_gate};\n    auto strict_lock=zbd_->StrictLock();',
        '''    GateAck gate_ack{batch_gate && active_gate?gc_gate:nullptr,active_gate};
    struct SpaceAck {std::function<void()> done;~SpaceAck(){done();}};
    SpaceAck space_ack{[&,sequence=active_pressure] {
      if (!sequence) return;
      Info(logger_,"受控空间维护完成：序号=%lu，整轮检查及维护已结束=1",sequence);
      if (!rrzc_boundary::State().Complete(sequence))
        Error(logger_,"受控空间确认序号不一致：序号=%lu",sequence);
    }};
    auto strict_lock=zbd_->StrictLock();''')
    return text
