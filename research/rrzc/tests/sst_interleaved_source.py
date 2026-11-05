"""仅在独立客户端加入默认关闭的持续交错请求，维护对象不改。"""
from calibration_source import once
from sst_lifetime_source import patched as original

LOOP = r'''
    if(interleaved) {
      auto read_one=[&](uint64_t window,uint64_t index) {
        const auto id=plan.ReadKey(window,index);GenerateKeyFromInt(id,FLAGS_num,&key);
        std::string value;const auto before=rrzc_rotating::Now();
        auto status=db_.db->Get(options,key,&value);const auto after=rrzc_rotating::Now();
        if(!status.ok() || value.size()!=4096)throw std::runtime_error("交错H读取失败或值大小不符");
        read_latency.push_back(after-before);trace.Record(false,id,before,after,rrzc_rotating::Now());
        thread->stats.FinishedOps(&db_,db_.db,1,kRead);
      };
      for(uint64_t window=0;window<plan.windows;++window) {
        for(uint64_t pass=0;pass<2;++pass) {
          uint64_t read_count=0;
          for(uint64_t n=0;n<plan.width-plan.hot;++n) {
            const auto id=plan.WriteKey(window,n);GenerateKeyFromInt(id,FLAGS_num,&key);
            const auto value=generator.Generate();const auto before=rrzc_rotating::Now();
            auto status=db_.db->Put(write_options_,key,value);const auto after=rrzc_rotating::Now();
            if(!status.ok())throw std::runtime_error(status.ToString());
            write_latency.push_back(after-before);trace.Record(true,id,before,after,rrzc_rotating::Now());
            thread->stats.FinishedOps(&db_,db_.db,1,kWrite);
            // 保留原每2048写Flush及等待，不改变单个写角色的请求顺序。
            if((n+1)%plan.chunk==0)drain(true);
            const uint64_t due=(n+1)*(4*plan.read_batch)/(plan.width-plan.hot);
            while(read_count<due) {
              read_one(window,pass*4*plan.read_batch+read_count++);
              if(read_count%plan.read_batch==0) {
                drain(false);
                checkpoint("交错H片段完成",window,pass*4+read_count/plan.read_batch-1);
              }
            }
          }
          drain(true);checkpoint("交错W覆盖完成",window,pass);
        }
      }
    } else {
'''


def patched(source):
    text = original(source)
    text = once(text, '    const auto persistent=rrzc_dbbench_hotspot::Number("RRZC_SST_LIFETIME_PERSISTENT_H",0);',
                '    const auto persistent=rrzc_dbbench_hotspot::Number("RRZC_SST_LIFETIME_PERSISTENT_H",0);\n'
                '    const auto interleaved=rrzc_dbbench_hotspot::Number("RRZC_SST_LIFETIME_INTERLEAVED",0);')
    text = once(text, 'if(smoke>1 || persistent>1 ||', 'if(smoke>1 || persistent>1 || interleaved>1 || (interleaved && !persistent) ||')
    text = once(text, '    if(uint64_t(FLAGS_num)!=plan.total)',
                '    fprintf(stderr,"SST生命周期交错：启用=%llu\\n",(unsigned long long)interleaved);\n'
                '    if(uint64_t(FLAGS_num)!=plan.total)')
    begin = '    for(uint64_t window=0;window<plan.windows;++window) {'
    end = '    drain(true);trace.Finish();'
    require_once = text.count(begin) == 1 and text.count(end) == 1
    if not require_once:
        raise ValueError('生命周期循环锚点不唯一')
    start = text.index(begin); stop = text.index(end, start)
    return text[:start] + LOOP + text[start:stop] + '    }\n' + text[stop:]
