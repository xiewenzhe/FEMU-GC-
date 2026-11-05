// 合成输入核对统一排序、未知不阻断全局、去折扣及无Greedy前置依赖。
#include "rrzc_direct_selector.h"
#include <cassert>
#include <iostream>
int main() {
  constexpr uint64_t m=1ULL<<20,s=rrzc_v1::kSecond;
  std::vector<rrzc_simple::Candidate> c={{1,11*m,256*m,0,4096,true,true,true},
      {2,48*m,256*m,0,4096,true,true,true}};
  std::map<uint64_t,rrzc_v1::RecentBlock> b;
  b[2].zone=2;b[2].rc=3919;b[2].delta10=213;b[2].flags=7;
  std::map<uint64_t,rrzc_retention::Forecast> f;
  f[2].known=true;f[2].interval10=10*s;f[2].interval30=30*s;
  f[2].loss30=13*m;
  auto run=[&]{return rrzc_direct::Rank(c,b,f,4096,192*m,10*s);};
  auto r=run();assert(r.size()==2 && r[0].candidate.zone==2 && r[0].cost.credit>40*m);
  assert(!r[1].cost.risk_known); // 空间最少区未知不阻断其他候选。
  f[2].known=false;r=run();assert(r[0].candidate.zone==1);
  b[2].rc=4096;r=run();assert(r[0].candidate.zone==2 && r[0].cost.net==0);
  b[1]=b[2];b[1].zone=1;r=run();assert(r[0].candidate.zone==1); // 两个紧迫区统一比较。
  b[2].flags=15;r=run();assert(r[1].cost.credit==0);
  c[0].covered=false;r=run();assert(r.size()==1 && r[0].candidate.zone==2);
  c[1].valid=65*m;r=run();assert(r.empty());
  // 无固定门槛时高有效量候选可以参加成本比较，历史非零预算仍可重放。
  r=rrzc_direct::Rank(c,b,f,4096,0,10*s);
  assert(r.size()==1 && r[0].candidate.valid==65*m);
  c[1].valid=256*m;
  assert(rrzc_direct::Rank(c,b,f,4096,0,10*s).empty());
  c[1].valid=255*m;c[1].target_available=false;
  assert(rrzc_direct::Rank(c,b,f,4096,0,10*s).empty());
  c[1].target_available=true;c[1].covered=false;
  assert(rrzc_direct::Rank(c,b,f,4096,0,10*s).empty());
  // 新128MiB配置：大有效量不再预先排除，由净复制/回收收益决定。
  std::vector<rrzc_simple::Candidate> open={{10,16*m,128*m,0,1024,true,true,true},
      {20,80*m,128*m,0,1024,true,true,true}};
  std::map<uint64_t,rrzc_v1::RecentBlock> hot;
  hot[20].zone=20;hot[20].rc=1000;hot[20].delta10=80;hot[20].flags=7;
  std::map<uint64_t,rrzc_retention::Forecast> kept;
  kept[20].known=true;kept[20].interval10=10*s;kept[20].interval30=30*s;
  auto broad=rrzc_direct::Rank(open,hot,kept,1024,0,10*s);
  assert(broad.size()==2 && broad[0].candidate.zone==20);
  kept[20].loss10=80*m;kept[20].loss30=80*m;
  broad=rrzc_direct::Rank(open,hot,kept,1024,0,10*s);
  assert(broad[0].candidate.zone==10);
  kept[20].known=false;
  assert(rrzc_direct::Rank(open,hot,kept,1024,0,10*s)[0].candidate.zone==10);
  rrzc_guard::Bounds bounds;bounds.Add(32*m,4096,0,256*m);
  rrzc_cost::ReserveObservation a;a.enabled=a.known=a.present=true;
  a.zone=1024*m;a.wp=a.zone;a.remaining=256*m;
  auto after=a;
  assert(rrzc_direct::SpareFits(2,bounds,32*m,256*m,a,after));
  // 对应失败条件：尾部不足、无普通空目标，但真实独占替补可承接全部片段。
  assert(!rrzc_direct::SpareFits(a.zone,bounds,32*m,256*m,a,after));
  after.host_generation++;assert(!rrzc_direct::SpareFits(2,bounds,32*m,256*m,a,after));
  after=a;after.remaining--;assert(!rrzc_direct::SpareFits(2,bounds,32*m,256*m,a,after));
  after=a;after.known=false;assert(!rrzc_direct::SpareFits(2,bounds,32*m,256*m,a,after));
  after=a;after.valid=4096;assert(!rrzc_direct::SpareFits(2,bounds,32*m,256*m,a,after));
  after=a;after.wp++;assert(!rrzc_direct::SpareFits(2,bounds,32*m,256*m,a,after));
  assert(!rrzc_direct::SpareFits(2,bounds,33*m,256*m,a,a));
  assert(!rrzc_direct::SpareFits(2,bounds,32*m,16*m,a,a));
  std::cout<<"统一排序、无Greedy前置、未知局部处理、原资格及去折扣边界通过\n";
}
