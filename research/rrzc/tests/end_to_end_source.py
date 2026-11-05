"""固定逻辑流＋真实有界队列，写请求仅在实际同步Flush成功后完成。"""
from calibration_source import once
import overlap_source
from read_cost_source import metrics

fs_header=overlap_source.fs_header
fs=overlap_source.fs
io=overlap_source.io
zbd=overlap_source.zbd
allowed_extra={'metrics.h'}
end_to_end=True


def client(text):
    text='#include "rrzc_end_to_end.h"\n'+overlap_source.client(text)
    start=text.index('  void RRZCSstLifetime(');end=text.index('  void RRZCRotating(',start)
    prefix,suffix=text[:start],text[end:];body=text[start:end]
    body=once(body,'前后台重叠配置：单前台=1，异步Flush=1，业务排空=0，批次GC=0，HIGH=%d，LOW=%d',
        '应用队列配置：单消费线程=1，同步批提交=1，Compaction排空=0，批次GC=0，HIGH=%d，LOW=%d')
    body=once(body,'    uint64_t stage=0;',
        '    const uint64_t queue_capacity=smoke?64:4096;\n'
        '    rrzc_end_to_end::Ledger application(queue_capacity,plan.chunk);\n'
        '    rrzc_end_to_end::Queue requests(plan,queue_capacity);\n'
        '    uint64_t stage=0;')
    # 同步Flush是本轮写完成语义，两臂统一；不等待Compaction才确认每批写。
    body=once(body,'flush.wait=settling;','flush.wait=true;')
    body=once(body,'        auto status=db_.db->Flush(flush);\n        if(!status.ok())throw std::runtime_error(status.ToString());',
        '        const auto flush_begin=rrzc_rotating::Now();\n'
        '        auto status=db_.db->Flush(flush);\n'
        '        const auto flush_end=rrzc_rotating::Now();\n'
        '        if(!status.ok())throw std::runtime_error(status.ToString());\n'
        '        application.Flushed(flush_begin,flush_end);')
    cut=body.index('    } else {',body.index('    if(interleaved)'))
    tail=body[cut:];body=body[:cut]
    body=once(body,'        const auto id=plan.ReadKey(window,index);GenerateKeyFromInt(id,FLAGS_num,&key);',
        '        const auto id=plan.ReadKey(window,index);\n'
        '        auto application_request=requests.Take(trace.Count(),false,id);\n'
        '        GenerateKeyFromInt(id,FLAGS_num,&key);')
    body=once(body,'        read_latency.push_back(after-before);',
        '        application.Api(application_request,before,after);\n'
        '        read_latency.push_back(after-before);')
    body=once(body,'            const auto id=plan.WriteKey(window,n);GenerateKeyFromInt(id,FLAGS_num,&key);',
        '            const auto id=plan.WriteKey(window,n);\n'
        '            auto application_request=requests.Take(trace.Count(),true,id);\n'
        '            GenerateKeyFromInt(id,FLAGS_num,&key);')
    body=once(body,'            write_latency.push_back(after-before);',
        '            application.Api(application_request,before,after);\n'
        '            write_latency.push_back(after-before);')
    tail=once(tail,'    drain(true);overlap_boundary(2);trace.Finish();',
        '    drain(true);overlap_boundary(2);application.Finish(plan.Reads()+plan.Writes());trace.Finish();')
    return prefix+body+tail+suffix


def wrapper(text):
    text=once(text,'单前台固定请求；异步Flush；独立后台；原10秒GC；请求结束后排空',
        '应用有界队列4096；写批次2048同步Flush后完成；独立后台；原10秒GC')
    return once(text,'RRZC_PHASE_TIME=',
        'RRZC_END_TO_END="${request_trace:+$result/应用完成.bin}" RRZC_PHASE_TIME=')
