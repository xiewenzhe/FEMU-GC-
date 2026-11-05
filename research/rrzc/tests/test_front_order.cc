// 合成键仅验证调度约束，不作为业务或性能结果。
#include "rrzc_front_order.h"
#include <atomic>
#include <cstdio>
#include <iostream>
#include <string>
#include <thread>
#include <vector>
int main() {
  rrzc_front_order::Order order;std::atomic<int> active{0};std::vector<unsigned> trace;
  auto run=[&](bool writer) {
    for(uint64_t n=0;n<(writer?3000:4000);++n) {
      order.Before(writer,n,n);
      if(++active!=1)std::abort();
      trace.push_back(writer?1:0);std::this_thread::yield();--active;
      order.ApiCompleted();
      order.After(writer,n);
    }
  };
  std::thread reader(run,false),writer(run,true);reader.join();writer.join();
  if(trace.size()!=7000)return 1;
  uint64_t hash=1469598103934665603ULL,ri=0,wi=0;
  for(size_t i=0;i<trace.size();++i) {
    const bool w=i%7>=4;if(trace[i]!=unsigned(w))return 2;
    hash=(hash^(w?wi++:ri++)^(w?0x57ULL:0x52ULL))*1099511628211ULL;
  }
  const auto check=order.Snapshot();if(check.operations!=7000 || check.hash!=hash)return 3;
  order.FinishTrace();
  char directory[]="/tmp/rrzc-gc-gate-XXXXXX";
  if(!mkdtemp(directory))return 4;
  const std::string base=std::string(directory)+"/tick";
  std::thread responder([&]{
    for(unsigned long long expected=1;expected<=2;++expected) {
      unsigned long long seen=0;
      while(true) {
        FILE* request=fopen((base+".request").c_str(),"r");
        if(request) {
          const bool valid=fscanf(request,"%llu",&seen)==1;
          fclose(request);
          if(valid && seen==expected)break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      }
      FILE* ack=fopen((base+".ack").c_str(),"w");
      if(!ack)std::abort();
      fprintf(ack,"%llu\n",expected);fclose(ack);
    }
  });
  rrzc_front_order::GcTick(base.c_str(),1);
  rrzc_front_order::GcTick(base.c_str(),2);
  responder.join();
  remove((base+".request").c_str());remove((base+".ack").c_str());rmdir(directory);
  std::cout<<"受控全局顺序回归通过：4读3写、无前台IO重叠、摘要一致\n";
}
