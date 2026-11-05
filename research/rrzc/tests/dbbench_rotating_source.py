"""向已验收热点客户端追加独立轮转基准，不更改旧ReadRandom和模型。"""
import difflib
from dbbench_hotspot_source import once

METHOD=r'''
  // 两线程固定批次汇合；等待不计入独立API延迟，不以维护结果驱动热点。
  void RRZCRotating(ThreadState* thread) {
    if(FLAGS_threads!=1 || thread->tid>1 || !db_.db || user_timestamp_size_)
      throw std::runtime_error("轮转负载仅支持单读单写和默认列族");
    const bool writer=thread->tid==0;
    const auto plan=rrzc_rotating::Plan::Load(reads_,writes_,FLAGS_num);
    const auto count=writer?plan.write_batch:plan.read_batch;
    const char* role=writer?"WRITE":"READ";
    std::unique_ptr<const char[]> guard;Slice key=AllocateKey(&guard);
    RandomGenerator generator;ReadOptions options=read_options_;
    std::vector<uint64_t> latency;latency.reserve(count*plan.rounds);
    uint64_t done=0,hash=1469598103934665603ULL,bytes=0;
    rrzc_rotating::Sync().Wait();
    for(uint64_t round=0;round<plan.rounds;++round) {
      const auto begin=rrzc_rotating::Now();
      for(uint64_t n=0;n<count;++n) {
        const auto id=plan.Key(writer,thread->rand.Next(),round,FLAGS_num);
        GenerateKeyFromInt(id,FLAGS_num,&key);hash=(hash^id)*1099511628211ULL;
        Status status;uint64_t before;
        if(writer) {
          const auto value=generator.Generate();before=rrzc_rotating::Now();
          status=db_.db->Put(write_options_,key,value);
          latency.push_back(rrzc_rotating::Now()-before);bytes+=key.size()+value.size();
        } else {
          std::string value;before=rrzc_rotating::Now();status=db_.db->Get(options,key,&value);
          latency.push_back(rrzc_rotating::Now()-before);bytes+=key.size()+value.size();
        }
        if(!status.ok()) {
          fprintf(stderr,"轮转真实业务失败：%s\n",status.ToString().c_str());db_bench_exit(1);
        }
        ++done;thread->stats.FinishedOps(&db_,db_.db,1,writer?kWrite:kRead);
      }
      const auto finished=rrzc_rotating::Now();
      fprintf(stderr,"RRZC批次完成：操作=%s，批次=%llu，累计=%llu，热点起点=%llu，键校验=%llu，开始纳秒=%llu，完成纳秒=%llu\n",
          role,(unsigned long long)round,(unsigned long long)done,
          (unsigned long long)plan.Base(round,FLAGS_num),(unsigned long long)hash,
          (unsigned long long)begin,(unsigned long long)finished);
      rrzc_rotating::Sync().Wait();
      if(writer && round%plan.phase_batches==plan.phase_batches-1)
        rrzc_dbbench_hotspot::Layout(db_.db,FLAGS_env->GetFileSystem().get(),FLAGS_fs_uri.find("zenfs://")==0);
      rrzc_rotating::Sync().Wait();
    }
    rrzc_rotating::Latency(role,latency);thread->stats.AddBytes(bytes);
  }
'''


def patched(source):
    result=once(source,'#include "tools/rrzc_dbbench_hotspot.h"',
        '#include "tools/rrzc_dbbench_hotspot.h"\n#include "tools/rrzc_rotating.h"')
    result=once(result,'      } else if (name == "readwhilewriting") {',
        '      } else if (name == "rrzcrotating") {\n'
        '        num_threads++;\n        method = &Benchmark::RRZCRotating;\n'
        '      } else if (name == "readwhilewriting") {')
    return once(result,'  void ReadWhileWriting(ThreadState* thread) {',
        METHOD+'\n  void ReadWhileWriting(ThreadState* thread) {')


def patch(source):
    return ''.join(difflib.unified_diff(source.splitlines(True),patched(source).splitlines(True),
        fromfile='a/tools/db_bench_tool.cc',tofile='b/tools/db_bench_tool.cc'))
