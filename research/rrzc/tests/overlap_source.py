"""单前台固定请求，后台自然运行；旧受控入口保留，选择器不变。"""
from calibration_source import once
import phase_time_source

zbd=phase_time_source.zbd
io=phase_time_source.io


def fs_header(text):
    return once(text,'  void GetZenFSSnapshot(ZenFSSnapshot& snapshot,',
        '  void ObserveOverlapBoundary(uint64_t stage,uint64_t operations,uint64_t captured_ns);\n\n'
        '  void GetZenFSSnapshot(ZenFSSnapshot& snapshot,')


def fs(text):
    text=phase_time_source.fs(text)
    return once(text,'void ZenFS::GetZenFSSnapshot(','''// 记录日志顺序边界；不等待GC、不强制维护、不修改选择器。
void ZenFS::ObserveOverlapBoundary(uint64_t stage,uint64_t operations,uint64_t captured_ns) {
  Info(logger_,"前后台重叠维护边界：阶段=%lu，请求=%lu，单调纳秒=%lu",stage,operations,captured_ns);
}

void ZenFS::GetZenFSSnapshot(''')


def client(text):
    text=phase_time_source.client(text)
    start=text.index('  void RRZCSstLifetime(');stop=text.index('  void RRZCRotating(',start)
    prefix,suffix=text[:start],text[stop:];body=text[start:stop]
    body=once(body,'    uint64_t stage=0;', '''    uint64_t stage=0;
    bool settling=false;
    const char* gate=getenv("RRZC_CAL_GC_GATE");
    if((gate && *gate) || rrzc_dbbench_hotspot::Number("RRZC_CAL_SINGLE_BACKGROUND",0) ||
       rrzc_dbbench_hotspot::Number("RRZC_CAL_DRAIN_BACKGROUND",0))
      throw std::runtime_error("重叠负载禁止批次GC门控和共享单后台/业务排空");
    auto* background_env=db_.db->GetEnv();
    const int high=background_env->GetBackgroundThreads(Env::Priority::HIGH);
    const int low=background_env->GetBackgroundThreads(Env::Priority::LOW);
    if(high<1 || low<1)throw std::runtime_error("重叠负载要求独立Flush与Compaction线程");
    fprintf(stderr,"前后台重叠配置：单前台=1，异步Flush=1，业务排空=0，批次GC=0，HIGH=%d，LOW=%d\\n",high,low);
    auto overlap_boundary=[&](uint64_t kind) {
      const uint64_t captured=rrzc_rotating::Now();
      fprintf(stderr,"前后台重叠业务边界：阶段=%llu，请求=%llu，单调纳秒=%llu\\n",
          (unsigned long long)kind,(unsigned long long)trace.Count(),(unsigned long long)captured);
      if(FLAGS_fs_uri.find("zenfs://")==0) {
        auto* fs=static_cast<ROCKSDB_NAMESPACE::ZenFS*>(FLAGS_env->GetFileSystem().get());
        fs->ObserveOverlapBoundary(kind,trace.Count(),captured);
      }
    };''')
    body=once(body,'    auto drain=[&](bool force_flush) {\n      if(force_flush) {',
        '    auto drain=[&](bool force_flush) {\n'
        '      if(!settling && !force_flush)return;\n'
        '      if(force_flush) {\n        flush.wait=settling;')
    body=once(body,'      rrzc_phase_time::Scope phase_compact(rrzc_phase_time::kCompactWait,0,trace.Count());',
        '      if(!settling)return;\n'
        '      rrzc_phase_time::Scope phase_compact(rrzc_phase_time::kCompactWait,0,trace.Count());')
    body=once(body,'''      { rrzc_phase_time::Scope phase_gc(rrzc_phase_time::kGcGate,0,trace.Count());
        rrzc_front_order::GcTick(getenv("RRZC_CAL_GC_GATE"),++stage); }''','      ++stage;')
    body=once(body,'    drain(true);trace.Finish();',
        '    overlap_boundary(1);settling=true;\n'
        '    drain(true);overlap_boundary(2);trace.Finish();')
    return prefix+body+suffix


def wrapper(text):
    text=once(text,'--max_background_jobs=2',
        '--max_background_jobs=2 --max_background_flushes=1 --max_background_compactions=1 '
        '--num_high_pri_threads=1 --num_low_pri_threads=1')
    text=once(text,'RRZC_CAL_DRAIN_BACKGROUND=1 RRZC_CAL_SINGLE_BACKGROUND=1',
        'RRZC_CAL_DRAIN_BACKGROUND=0 RRZC_CAL_SINGLE_BACKGROUND=0')
    text=once(text,'gc_gate="$result/受控GC"','gc_gate=""')
    # 禁止编译包装器保留旧门控路径，在启动业务前记录真实开关。
    return once(text,'flush_w=1;hotspot=1;phase run',
        'printf "单前台固定请求；异步Flush；独立后台；原10秒GC；请求结束后排空\\n" > "$result/重叠配置.txt"\n'
        'flush_w=1;hotspot=1;phase run')
