// 合成边界测试，不是设备实验；旧候选行为必须逐项保持一致。
#include "rrzc_candidate_pool.h"
#include "rrzc_weighted_selector.h"
#include <cassert>
#include <iostream>

int main() {
  assert(rrzc_pool::Parse(nullptr,false).valid);
  assert(!rrzc_pool::Parse(nullptr,true).expanded);
  assert(!rrzc_pool::Parse("original",true).expanded);
  assert(rrzc_pool::Parse("expanded",true).expanded);
  assert(!rrzc_pool::Parse("expanded",false).valid);
  assert(!rrzc_pool::Parse("",true).valid);
  assert(!rrzc_pool::Parse("1",true).valid);
  for(uint64_t cap=0;cap<=128;++cap) for(uint64_t v=0;v<=130;++v)
    for(uint64_t gate: {0ULL,67ULL,79ULL,91ULL,100ULL}) for(bool exact: {false,true}) {
      const uint64_t garbage=cap && v<=cap ? 100-100*v/cap : 0;
      assert(rrzc_pool::Selected(v,cap,garbage,gate,exact,false)==
             rrzc_exact::Selected(v,cap,garbage,gate,exact));
      assert(rrzc_pool::Selected(v,cap,garbage,gate,exact,true)==(cap && v>0 && v<cap));
    }
  assert(!rrzc_pool::Selected(46,100,54,67,true,false));
  assert(rrzc_pool::Selected(46,100,54,67,true,true));
  rrzc_simple::Candidate c;c.capacity=100;c.valid=46;c.covered=true;c.target_available=true;
  assert(rrzc_simple::Eligible(c,0));
  c.covered=false;assert(!rrzc_simple::Eligible(c,0));
  c.covered=true;c.target_available=false;assert(!rrzc_simple::Eligible(c,0));
  std::cout<<"候选范围合成边界通过：默认行为、正回收及公共安全资格不变\n";
}
