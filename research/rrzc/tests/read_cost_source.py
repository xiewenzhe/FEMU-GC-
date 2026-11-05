"""只拆分读等待和记录开销；锁及严格4KiB命令正文不变。"""
from calibration_source import once
import overlap_source
fs_header=overlap_source.fs_header
fs=overlap_source.fs
zbd=overlap_source.zbd
allowed_extra={'zbdlib_zenfs.cc','metrics.h'}


def metrics(text):
    text='#include <chrono>\n'+text
    return once(text,'  virtual uint64_t GetTime() { return env_->NowMicros(); }',
        '  // 仅耗时统计改用单调时钟，保留原有非负断言；不改IO或维护路径。\n'
        '  virtual uint64_t GetTime() { return std::chrono::duration_cast<std::chrono::microseconds>(\n'
        '      std::chrono::steady_clock::now().time_since_epoch()).count(); }')


def io(text):
    text=overlap_source.io(text)
    text='#include "rrzc_read_cost.h"\n'+text
    text=once(text,'  return rrzc_strict::ReadWithMaintenance(',
        '  rrzc_read_cost::Read read_cost(file_id_,n,rrzc_strict::migration);\n'
        '  return rrzc_strict::ReadWithMaintenance(')
    text=once(text,'[&]() {return PositionedReadOnce(offset,n,result,scratch,direct);}',
        '[&]() {auto status=PositionedReadOnce(offset,n,result,scratch,direct);read_cost.Done();return status;}')
    return once(text,'[&]() {return zbd_->StrictLock();}',
        '[&]() {read_cost.BeforeLock();auto guard=zbd_->StrictLock();read_cost.Acquired();return guard;}')


def zbdlib(text):
    text='#include "rrzc_read_cost.h"\n'+text
    return once(text,'      const int status=ioctl(read_f_,NVME_IOCTL_IO_CMD,&cmd);',
        '      const auto read_cost_start=rrzc_read_cost::IoBegin();\n'
        '      const int status=ioctl(read_f_,NVME_IOCTL_IO_CMD,&cmd);\n'
        '      rrzc_read_cost::IoEnd(read_cost_start);')


def client(text):
    text='#include "rrzc_read_cost.h"\n'+overlap_source.client(text)
    start=text.index('  void RRZCSstLifetime(');stop=text.index('  void RRZCRotating(',start)
    prefix,suffix=text[:start],text[stop:];body=text[start:stop]
    cut=body.index('    } else {',body.index('    if(interleaved)'))
    original_other_branch=body[cut:];body=body[:cut]
    body=once(body,'        std::string value;const auto before=rrzc_rotating::Now();',
        '        rrzc_read_cost::Begin(trace.Count(),id);\n'
        '        std::string value;const auto before=rrzc_rotating::Now();')
    body=once(body,'        if(!status.ok() || value.size()!=4096)throw std::runtime_error("交错H读取失败或值大小不符");',
        '        rrzc_read_cost::End(before,after);\n'
        '        if(!status.ok() || value.size()!=4096)throw std::runtime_error("交错H读取失败或值大小不符");')
    for role,indent in (('read','        '),('write','            ')):
        write=role=='write';key='true' if write else 'false'
        old=f'{indent}{role}_latency.push_back(after-before);trace.Record({key},id,before,after,rrzc_rotating::Now());'
        body=once(body,old,indent+'{ rrzc_read_cost::Work cost(1,trace.Count());\n'+old+' }')
        old=indent+f'thread->stats.FinishedOps(&db_,db_.db,1,k{"Write" if write else "Read"});'
        body=once(body,old,indent+'{ rrzc_read_cost::Work cost(2,trace.Count());\n'+old+' }')
    body=once(body,'            const auto value=generator.Generate();const auto before=rrzc_rotating::Now();',
        '            const auto value=[&](){rrzc_read_cost::Work cost(0,trace.Count());return generator.Generate();}();\n'
        '            const auto before=rrzc_rotating::Now();')
    body=once(body,'      fprintf(stderr,"SST生命周期阶段：序号=',
        '      rrzc_read_cost::Work phase_output(3,trace.Count());\n'
        '      fprintf(stderr,"SST生命周期阶段：序号=')
    return prefix+body+original_other_branch+suffix
