// 合成单元输入只检验公式边界，不是FEMU收益数据；也支持真实日志逐轮重放。
#include "rrzc_total_cost_selector.h"
#include <cassert>
#include <iostream>
#include <string>

static rrzc_direct::Row row(uint64_t zone,uint64_t valid,uint64_t capacity,uint64_t credit,bool safe=true) {
  rrzc_direct::Row r;r.candidate={zone,valid,capacity,0,1024,true,true,true};
  r.cost.net=valid-credit;r.cost.credit=credit;r.cost.reclaimed=capacity-valid;r.safe=safe;
  return r;
}
static void unit() {
  constexpr uint64_t m=1ULL<<20;
  auto result=rrzc_total::FromRows({row(1,4*m,128*m,0),row(2,5*m,128*m,5*m)});
  assert(result.known && result.rows[0].original.candidate.zone==2);
  assert(result.rows[0].total_copy==5*m && result.rows[1].total_copy==9*m);
  // 原净成本可把紧迫候选减成零；新评分仍比较实际复制和空间收益。
  auto cold=row(1,4*m,128*m,0),costly=row(2,24*m,128*m,24*m);
  assert(rrzc_direct::Less(costly,cold));
  result=rrzc_total::FromRows({cold,costly});
  assert(result.known && result.rows[0].original.candidate.zone==1);
  assert(result.rows[0].total_copy==28*m && result.rows[1].total_copy==24*m);
  // 对突然退役没有观测证据时，新核仍会误选8MiB；不偷偷加未来删除标签。
  result=rrzc_total::FromRows({cold,row(2,8*m,128*m,8*m)});
  assert(result.known && result.rows[0].original.candidate.zone==2);
  result=rrzc_total::FromRows({cold,row(2,5*m,128*m,0)});
  assert(result.rows[0].original.candidate.zone==1);
  result=rrzc_total::FromRows({cold,row(2,24*m,128*m,24*m,false)});
  assert(result.known && result.pool_rr==0 && result.rows.size()==1);
  assert(!rrzc_total::FromRows({cold,cold}).known);
  auto invalid=cold;invalid.cost.reclaimed--;
  assert(!rrzc_total::FromRows({invalid}).known);
  invalid=cold;invalid.candidate.valid=invalid.candidate.capacity;
  assert(!rrzc_total::FromRows({invalid}).known);
  const uint64_t high=UINT64_MAX/2;
  assert(!rrzc_total::FromRows({row(1,high,UINT64_MAX,high),row(2,high,UINT64_MAX,high),
                              row(3,high,UINT64_MAX,high)}).known);
  assert(!rrzc_total::FromRows({row(1,UINT64_MAX-1,UINT64_MAX,0),row(2,3,4,3)}).known);
  result=rrzc_total::FromRows({});assert(result.known && result.rows.empty());
  std::cout<<"总复制排序、低回收反例、无法预知退役边界及溢出检查通过\n";
}
int main(int argc,char** argv) {
  if(argc==1){unit();return 0;}
  if(argc!=2 || std::string(argv[1])!="replay")return 2;
  uint64_t count=0;
  if(!(std::cin>>count) || count>100000)return 2;
  std::vector<rrzc_direct::Row> inputs;
  for(uint64_t i=0;i<count;++i) {
    uint64_t zone,valid,capacity,credit,reclaimed,net,safe;
    if(!(std::cin>>zone>>valid>>capacity>>credit>>reclaimed>>net>>safe) || safe>1)return 2;
    auto r=row(zone,valid,capacity,0,safe);r.cost.credit=credit;r.cost.reclaimed=reclaimed;r.cost.net=net;
    inputs.push_back(r);
  }
  const auto result=rrzc_total::FromRows(inputs);
  std::cout<<result.known<<' '<<result.pool_rr<<' '<<result.rows.size()<<'\n';
  for(const auto& r:result.rows)
    std::cout<<r.original.candidate.zone<<' '<<r.deferred_rr<<' '<<r.total_copy<<'\n';
  return result.known ? 0 : 1;
}
