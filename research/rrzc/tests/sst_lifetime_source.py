"""仅向独立db_bench副本添加顺序生命周期入口；旧负载和ZenFS对象保持原字节。"""
from calibration_source import once

METHOD=r'''
  void RRZCSstLifetime(ThreadState* thread) {
    RRZCSeedTls(thread);
    const auto smoke=rrzc_dbbench_hotspot::Number("RRZC_SST_LIFETIME_SMOKE",0);
    const auto persistent=rrzc_dbbench_hotspot::Number("RRZC_SST_LIFETIME_PERSISTENT_H",0);
    if(smoke>1 || persistent>1 || FLAGS_threads!=1 || thread->tid!=0 || !db_.db || user_timestamp_size_ || FLAGS_value_size!=4096)
      throw std::runtime_error("生命周期入口仅支持单前台、4KiB及默认列族");
    const auto plan=rrzc_sst_lifetime::Plan::Make(smoke==1,persistent==1);
    fprintf(stderr,"SST生命周期计划：固定H=%llu\n",(unsigned long long)persistent);
    if(uint64_t(FLAGS_num)!=plan.total)throw std::runtime_error("生命周期总键数与预注册不符");
    rrzc_sst_lifetime::Trace trace(getenv("RRZC_CAL_REQUEST_TRACE"));
    std::unique_ptr<const char[]> guard;Slice key=AllocateKey(&guard);
    RandomGenerator generator;ReadOptions options=read_options_;
    FlushOptions flush;flush.wait=true;
    std::vector<uint64_t> read_latency,write_latency;
    uint64_t stage=0;
    auto drain=[&](bool force_flush) {
      if(force_flush) {
        auto status=db_.db->Flush(flush);
        if(!status.ok())throw std::runtime_error(status.ToString());
      }
      auto status=db_.db->WaitForCompact(WaitForCompactOptions());
      if(!status.ok())throw std::runtime_error(status.ToString());
    };
    auto checkpoint=[&](const char* role,uint64_t window,uint64_t pass) {
      rrzc_front_order::GcTick(getenv("RRZC_CAL_GC_GATE"),++stage);
      rrzc_dbbench_hotspot::Layout(db_.db,FLAGS_env->GetFileSystem().get(),FLAGS_fs_uri.find("zenfs://")==0);
      fprintf(stderr,"SST生命周期阶段：序号=%llu，阶段=%s，窗口=%llu，段=%llu，成功请求=%llu，纳秒=%llu\n",
          (unsigned long long)stage,role,(unsigned long long)window,(unsigned long long)pass,
          (unsigned long long)trace.Count(),(unsigned long long)rrzc_rotating::Now());
    };
    for(uint64_t window=0;window<plan.windows;++window) {
      for(uint64_t pass=0;pass<2;++pass) {
        for(uint64_t n=0;n<plan.width-plan.hot;++n) {
          const auto id=plan.WriteKey(window,n);GenerateKeyFromInt(id,FLAGS_num,&key);
          const auto value=generator.Generate();const auto before=rrzc_rotating::Now();
          auto status=db_.db->Put(write_options_,key,value);const auto after=rrzc_rotating::Now();
          if(!status.ok())throw std::runtime_error(status.ToString());
          write_latency.push_back(after-before);trace.Record(true,id,before,after,rrzc_rotating::Now());
          thread->stats.FinishedOps(&db_,db_.db,1,kWrite);
          // 显式Flush是本受控负载的一部分；不主动CompactRange或删除SST。
          if((n+1)%plan.chunk==0)drain(true);
        }
        drain(true);checkpoint("W覆盖完成",window,pass);
      }
      for(uint64_t batch=0;batch<8;++batch) {
        for(uint64_t n=0;n<plan.read_batch;++n) {
          const auto id=plan.ReadKey(window,batch*plan.read_batch+n);GenerateKeyFromInt(id,FLAGS_num,&key);
          std::string value;const auto before=rrzc_rotating::Now();
          auto status=db_.db->Get(options,key,&value);const auto after=rrzc_rotating::Now();
          if(!status.ok() || value.size()!=4096)throw std::runtime_error("H读取失败或值大小不符");
          read_latency.push_back(after-before);trace.Record(false,id,before,after,rrzc_rotating::Now());
          thread->stats.FinishedOps(&db_,db_.db,1,kRead);
        }
        checkpoint("H读取完成",window,batch);
      }
    }
    drain(true);trace.Finish();
    if(read_latency.size()!=plan.Reads() || write_latency.size()!=plan.Writes())throw std::runtime_error("生命周期请求数不符");
    rrzc_rotating::Latency("READ",read_latency);rrzc_rotating::Latency("WRITE",write_latency);
    fprintf(stderr,"SST生命周期完成：读=%llu，写=%llu，检查点=%llu，正常Compaction=1，强制CompactRange=0，外部删除SST=0\n",
      (unsigned long long)read_latency.size(),(unsigned long long)write_latency.size(),(unsigned long long)stage);
  }
'''


def patched(source):
    if 'void RRZCSstLifetime(' in source:
        raise ValueError('生命周期入口已存在，拒绝重复插入')
    text=once(source,'#include "rrzc_front_order.h"',
        '#include "rrzc_front_order.h"\n#include "rrzc_sst_lifetime.h"')
    text=once(text,'      } else if (name == "rrzcrotating") {',
        '      } else if (name == "rrzcsstlifetime") {\n'
        '        method = &Benchmark::RRZCSstLifetime;\n'
        '      } else if (name == "rrzcrotating") {')
    return once(text,'  void RRZCRotating(ThreadState* thread) {',METHOD+'\n  void RRZCRotating(ThreadState* thread) {')


def wrapper(source,persistent_h=False):
    text=once(source,'rrzcrotating,rrzctick,flush','rrzcsstlifetime,rrzctick,flush')
    text=once(text,'--reads=786432 --writes=589824','--reads=393216 --writes=737280')
    if persistent_h:
        text=once(text,'RRZC_ROTATE_ROUNDS=24','RRZC_SST_LIFETIME_PERSISTENT_H=1 RRZC_ROTATE_ROUNDS=24')
    text=once(text,'hotspot=1;snapshot before',
        'hotspot=1;snapshot before\ncp "$result/strict.before.json" "$result/strict.preparation.before.json"')
    # 仅新生命周期入口修正设备计数窗口：准备读单列，不混入业务维护增量。
    return once(text,'layout_prefix=$result/布局',
        'python3 "$target/strict_stats_guest.py" > "$result/strict.before.json"\nlayout_prefix=$result/布局')
