// 从离线归档输入重放同一个C++选择器，不模拟设备后果或RR节省次数。
#include "rrzc_net_cost_selector.h"
#include "rrzc_benefit_budget.h"
#include <iostream>

int main(int argc,char** argv) {
  uint64_t budget=16ULL<<20;
  bool benefit_budget=false;
  // 仅离线诊断：保留192MiB回收资格，64MiB覆盖所有可能合格候选的有效量。
  // 这不是客机配置开关，也不改变选择器的默认参数。
  if (argc==2 && std::string(argv[1])=="--diagnose-without-extra-cap")
    budget=(256ULL-192ULL)<<20;
  else if (argc==2 && std::string(argv[1])=="--benefit-budget") benefit_budget=true;
  else if (argc!=1) return 6;
  uint64_t rid,n;
  while (std::cin>>rid) {
    if (!(std::cin>>n) || !n || n>65536) return 2;
    std::vector<rrzc_simple::Candidate> cs;
    std::map<uint64_t,rrzc_v1::RecentBlock> blocks;
    std::map<uint64_t,rrzc_v1::GrowthEvidence> growth;
    std::map<uint64_t,rrzc_retention::Forecast> forecasts;
    for (uint64_t i=0;i<n;++i) {
      rrzc_simple::Candidate c;rrzc_v1::RecentBlock b;
      rrzc_v1::GrowthEvidence g;rrzc_retention::Forecast f;bool usable;
      if (!(std::cin>>c.zone>>c.valid>>c.capacity>>c.covered>>c.target_available>>usable
          >>b.rc>>b.delta10>>b.delta30>>b.flags>>g.known>>g.delta>>g.interval_ns
          >>g.predicted_ns>>f.known>>f.loss10>>f.loss30>>f.interval10>>f.interval30>>f.retained)) return 3;
      if (growth.count(c.zone)) return 4;
      c.threshold=4096;c.maximum_reads=b.rc;c.pressure_available=usable;b.zone=c.zone;
      cs.push_back(c);if (usable) blocks.emplace(c.zone,b);
      growth.emplace(c.zone,g);forecasts.emplace(c.zone,f);
    }
    rrzc_net_cost::Report r;
    uint64_t guard_budget=budget;
    if (benefit_budget) {
      const auto report=rrzc_benefit_budget::Evaluate(cs,blocks,growth,forecasts,
          4096,192ULL<<20,10*rrzc_v1::kSecond,16ULL<<20);
      r=report.net;guard_budget=report.guard_budget;
    } else r=rrzc_net_cost::Evaluate(cs,blocks,growth,forecasts,4096,192ULL<<20,10*rrzc_v1::kSecond,budget);
    const auto& d=r.decision;
    std::cout<<"{\"轮次\":"<<rid<<",\"GreedyZone\":"<<d.greedy.selected.zone
      <<",\"建议Zone\":"<<d.proposed_zone<<",\"会改选\":"<<d.would_switch
      <<",\"额外字节\":"<<d.extra_valid<<",\"预测纳秒\":"<<d.predicted_ns
      <<",\"原因\":\""<<d.reason<<"\"";
    if (benefit_budget) std::cout<<",\"执行预算字节\":"<<guard_budget;
    std::cout<<",\"成本\":[";
    bool comma=false;
    for (const auto& item:r.costs) {
      const auto& c=item.second;if(comma)std::cout<<",";comma=true;
      std::cout<<"{\"Zone\":"<<item.first<<",\"可知\":"<<c.known<<",\"ETA纳秒\":"<<c.eta_ns
        <<",\"预计保留字节\":"<<c.retained<<",\"折扣抵扣字节\":"<<c.credit
        <<",\"净复制字节\":"<<c.net_copy<<",\"回收字节\":"<<c.reclaimed
        <<",\"原因\":\""<<c.reason<<"\"}";
    }
    std::cout<<"]}\n";
  }
  return std::cin.eof()?0:5;
}
