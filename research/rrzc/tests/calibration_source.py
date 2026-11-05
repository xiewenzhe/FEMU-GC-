"""独立校准客户端：保留键生成与IO，按显式开关增加种子及全局顺序控制。"""
def once(text,before,after):
    if text.count(before)!=1:
        raise ValueError('校准源码锚点不唯一：'+before[:70])
    return text.replace(before,after,1)

METHOD = r'''
  // 仅在显式实验开关下重置RocksDB线程本地随机数；独立于ThreadState的请求生成器。
  void RRZCSeedTls(ThreadState* thread) {
    const auto enabled=rrzc_dbbench_hotspot::Number("RRZC_CAL_TLS_SEED",0);
    if(enabled>1)throw std::runtime_error("内部随机种子开关只接受0或1");
    if(!enabled)return;
    if(FLAGS_seed<=0)throw std::runtime_error("内部随机性控制要求显式正种子");
    const auto seed=rrzc_tls_seed::Apply(static_cast<uint64_t>(FLAGS_seed),thread->tid);
    fprintf(stderr,"内部随机数固定：线程=%d，种子=%u，请求随机数独立=1\n",thread->tid,seed);
  }
  // 每段小于原16MiB写缓冲；显式落盘并等待已有压缩，不改变业务期配置。
  void RRZCCalibratedLoad(ThreadState* thread) {
    const auto chunk=rrzc_dbbench_hotspot::Number("RRZC_CAL_LOAD_CHUNK",2048);
    if(FLAGS_threads!=1 || thread->tid!=0 || !db_.db || user_timestamp_size_ ||
        FLAGS_value_size!=4096 || chunk!=2048 || FLAGS_num<=0)
      throw std::runtime_error("校准装载只支持单线程、4KiB值及2048键段");
    RRZCSeedTls(thread);
    std::unique_ptr<const char[]> guard;Slice key=AllocateKey(&guard);
    RandomGenerator generator;FlushOptions flush;flush.wait=true;
    uint64_t hash=1469598103934665603ULL;
    for(uint64_t first=0;first<static_cast<uint64_t>(FLAGS_num);first+=chunk) {
      const auto end=std::min(first+chunk,static_cast<uint64_t>(FLAGS_num));
      for(uint64_t n=first;n<end;++n) {
        GenerateKeyFromInt(n,FLAGS_num,&key);
        auto status=db_.db->Put(write_options_,key,generator.Generate());
        if(!status.ok())throw std::runtime_error(status.ToString());
        hash=(hash^n)*1099511628211ULL;
        thread->stats.FinishedOps(&db_,db_.db,1,kWrite);
      }
      auto status=db_.db->Flush(flush);
      if(!status.ok())throw std::runtime_error(status.ToString());
      status=db_.db->WaitForCompact(WaitForCompactOptions());
      if(!status.ok())throw std::runtime_error(status.ToString());
      fprintf(stderr,"校准装载段：起始键=%llu，结束键=%llu，累计键摘要=%llu，纳秒=%llu\n",
          (unsigned long long)first,(unsigned long long)end,(unsigned long long)hash,
          (unsigned long long)rrzc_rotating::Now());
    }
  }
  // 序号对应新包装器预注册的基准边界，保持原生flush和waitforcompaction方法不变。
  void RRZCCalibrationMark(ThreadState*) {
    static unsigned sequence=0;
    fprintf(stderr,"校准阶段边界：序号=%u，纳秒=%llu\n",sequence++,
        (unsigned long long)rrzc_rotating::Now());
  }
  // 只生成键摘要，不访问数据库；与旧轮全部48个批次对照，防止计时改变随机序列。
  void RRZCCalibrationTrace(ThreadState* thread) {
    const bool writer=thread->tid==0;
    const auto plan=rrzc_rotating::Plan::Load(reads_,writes_,FLAGS_num);
    const auto count=writer?plan.write_batch:plan.read_batch;
    uint64_t hash=1469598103934665603ULL;
    for(uint64_t round=0;round<plan.rounds;++round) {
      for(uint64_t n=0;n<count;++n)
        hash=(hash^plan.Key(writer,thread->rand.Next(),round,FLAGS_num))*1099511628211ULL;
      fprintf(stderr,"校准请求摘要：操作=%s，批次=%llu，累计=%llu，热点起点=%llu，键校验=%llu\n",
          writer?"WRITE":"READ",(unsigned long long)round,(unsigned long long)((round+1)*count),
          (unsigned long long)plan.Base(round,FLAGS_num),(unsigned long long)hash);
    }
  }
'''


def patched(source):
    text=once(source,'#include "tools/rrzc_rotating.h"',
        '#include "tools/rrzc_rotating.h"\n#include "rrzc_tls_seed.h"\n#include "rrzc_front_order.h"')
    text=once(text,'      } else if (name == "rrzcrotating") {',
        '      } else if (name == "rrzccalload") {\n'
        '        fresh_db = true;\n        method = &Benchmark::RRZCCalibratedLoad;\n'
        '      } else if (name == "rrzctick") {\n'
        '        method = &Benchmark::RRZCCalibrationMark;\n'
        '      } else if (name == "rrzctrace") {\n'
        '        num_threads++;\n        method = &Benchmark::RRZCCalibrationTrace;\n'
        '      } else if (name == "rrzcrotating") {')
    text=once(text,'      total_thread_count_++;\n      arg[i].thread = new ThreadState(i, total_thread_count_);',
        '      // 时间标记不消耗原生线程种子编号，其他基准行为保持不变。\n'
        '      if (method != &Benchmark::RRZCCalibrationMark) total_thread_count_++;\n'
        '      arg[i].thread = new ThreadState(i, total_thread_count_);')
    text=once(text,'  void RRZCRotating(ThreadState* thread) {',
        METHOD+'\n  void RRZCRotating(ThreadState* thread) {\n    RRZCSeedTls(thread);')
    text=once(text,OPEN_END,OPEN_END[:-4]+SERIAL_BACKGROUND+'  }\n')
    start=text.index('  void RRZCRotating(ThreadState* thread) {')
    end=text.index('  void ReadWhileWriting(ThreadState* thread) {',start)
    body=text[start:end]
    for old,new in ORDER_HOOKS:
        body=once(body,old,new)
    return text[:start]+body+text[end:]


OPEN_END='''    if (!s.ok()) {
      fprintf(stderr, "open error: %s\\n", s.ToString().c_str());
      db_bench_exit(1);
    }
  }
'''

SERIAL_BACKGROUND=r'''
    // 打开时的选项修正会重新建立HIGH线程池；仅受控实验在打开完成后重设。
    const auto serial_background=rrzc_dbbench_hotspot::Number("RRZC_CAL_SINGLE_BACKGROUND",0);
    if(serial_background>1)throw std::runtime_error("后台串行开关只接受0或1");
    if(serial_background) {
      if(FLAGS_num_column_families!=1 || FLAGS_readonly || FLAGS_num_multi_db>1)
        throw std::runtime_error("后台串行校准仅支持单实例单列族读写数据库");
      auto paused=db->db->PauseBackgroundWork();
      if(!paused.ok())throw std::runtime_error(paused.ToString());
      auto* env=options.env;
      env->SetBackgroundThreads(0,Env::Priority::HIGH);
      env->SetBackgroundThreads(1,Env::Priority::LOW);
      const bool configured=env->GetBackgroundThreads(Env::Priority::HIGH)==0 &&
          env->GetBackgroundThreads(Env::Priority::LOW)==1;
      auto resumed=db->db->ContinueBackgroundWork();
      if(!configured || !resumed.ok())throw std::runtime_error("共享后台线程设置失败");
      fprintf(stderr,"受控后台线程：HIGH=0，LOW=1，Flush与Compaction共用单线程\n");
    }
'''


ORDER_HOOKS=[
    ('    const auto count=writer?plan.write_batch:plan.read_batch;',
     '    const bool ordered=rrzc_dbbench_hotspot::Number("RRZC_CAL_FRONT_ORDER",0)==1;\n'
     '    const auto count=writer?plan.write_batch:plan.read_batch;\n'
     '    if(ordered && count%(writer?3:4))throw std::runtime_error("受控批次必须满足4读3写分组");'),
    ('        GenerateKeyFromInt(id,FLAGS_num,&key);hash=(hash^id)*1099511628211ULL;',
     '        if(ordered)rrzc_front_order::State().Before(writer,round*count+n,id);\n'
     '        GenerateKeyFromInt(id,FLAGS_num,&key);hash=(hash^id)*1099511628211ULL;'),
    ('        ++done;thread->stats.FinishedOps(&db_,db_.db,1,writer?kWrite:kRead);',
     '        if(ordered)rrzc_front_order::State().ApiCompleted();\n'
     '        if(writer && rrzc_dbbench_hotspot::Number("RRZC_CAL_DRAIN_BACKGROUND",0)) {\n'
     '          const auto drain_begin=rrzc_rotating::Now();\n'
     '          auto drained=db_.db->WaitForCompact(WaitForCompactOptions());\n'
     '          drain_ns+=rrzc_rotating::Now()-drain_begin;\n'
     '          if(!drained.ok())throw std::runtime_error(drained.ToString());\n'
     '        }\n'
     '        if(ordered)rrzc_front_order::State().After(writer,round*count+n);\n'
     '        ++done;thread->stats.FinishedOps(&db_,db_.db,1,writer?kWrite:kRead);'),
    ('    uint64_t done=0,hash=1469598103934665603ULL,bytes=0;',
     '    uint64_t done=0,hash=1469598103934665603ULL,bytes=0;\n'
     '    uint64_t drain_ns=0;\n'
     '    if(rrzc_dbbench_hotspot::Number("RRZC_CAL_DRAIN_BACKGROUND",0)>1)\n'
     '      throw std::runtime_error("后台排空开关只接受0或1");'),
    ('    rrzc_rotating::Latency(role,latency);thread->stats.AddBytes(bytes);',
     '    if(ordered && writer)rrzc_front_order::State().FinishTrace();\n'
     '    if(writer && rrzc_dbbench_hotspot::Number("RRZC_CAL_DRAIN_BACKGROUND",0))\n'
     '      fprintf(stderr,"受控后台排空：Put次数=%llu，累计纳秒=%llu，不主动Flush=1\\n",\n'
     '          (unsigned long long)done,(unsigned long long)drain_ns);\n'
     '    rrzc_rotating::Latency(role,latency);thread->stats.AddBytes(bytes);'),
    ('      if(writer && round%plan.phase_batches==plan.phase_batches-1)',
     '      if(ordered && writer) {\n'
     '        const auto point=rrzc_front_order::State().Snapshot();\n'
     '        fprintf(stderr,"全局回放检查点：批次=%llu，操作数=%llu，摘要=%llu\\n",\n'
     '            (unsigned long long)round,(unsigned long long)point.operations,(unsigned long long)point.hash);\n'
     '        rrzc_front_order::GcTick(getenv("RRZC_CAL_GC_GATE"),round+1);\n'
     '      }\n'
     '      if(writer && round%plan.phase_batches==plan.phase_batches-1)')]


def without_hooks(text):
    for old,new in reversed(ORDER_HOOKS):text=once(text,new,old)
    return once(text,'\n    RRZCSeedTls(thread);','')


def wrapper(base):
    from run_repeat_order_host import tagged_wrapper
    text=tagged_wrapper(base)
    text=once(text,'RRZC_DB_LAYOUT_PREFIX="$result/布局"',
        'RRZC_DB_LAYOUT_PREFIX="${layout_prefix:-$result/布局}" RRZC_CAL_LOAD_CHUNK=2048')
    text=once(text,'hotspot=0;phase load\nrun_bench --benchmarks=fillseq,flush,waitforcompaction',
        'hotspot=1;layout_prefix=$result/装载;phase load\nrun_bench --benchmarks=rrzccalload,flush,waitforcompaction,rrzclayout')
    text=once(text,'seal_log load;snapshot before\nhotspot=1;phase run',
        '''seal_log load;snapshot before
python3 "$target/calibration_guest.py" blocks > "$result/blocks.before.json"
python3 "$target/calibration_guest.py" gate "$result" "$target/g1" > "$result/起始核验.json"
layout_prefix=$result/布局
hotspot=1;phase run''')
    return once(text,'--benchmarks=rrzclayout,rrzcrotating,flush,waitforcompaction,rrzclayout',
        '--benchmarks=rrzctick,rrzclayout,rrzctick,rrzcrotating,rrzctick,flush,rrzctick,waitforcompaction,rrzctick,rrzclayout,rrzctick')
