// 测试计划边界的内存验证；不是设备结果。
#include "rocksdb_observe_plan.h"
#include <cassert>
#include <iostream>
int main() {
  rrzc_real::Plan plan;
  assert(rrzc_real::Parse(nullptr,&plan)&&plan.keys==524288);
  assert(rrzc_real::Parse("pressure512",&plan)&&plan.keys==655360);
  assert(std::strcmp(plan.label,"rocksdb-pressure-20261016")==0);
  assert((plan.keys-rrzc_real::BaseKeys)*4096ULL==512ULL*1024*1024);
  assert(rrzc_real::ColdKey==524287&&rrzc_real::ColdKey<rrzc_real::BaseKeys);
  assert(!rrzc_real::Parse("pressure1024",&plan));
  assert(rrzc_real::Parse("recent90",&plan)&&plan.keys==655360&&plan.write_seconds==90);
  assert(std::strcmp(plan.label,"rocksdb-recent-20261016")==0);
  assert(rrzc_real::Parse("pair90",&plan)&&plan.keys==655360&&plan.write_seconds==90);
  assert(std::strcmp(plan.label,"rocksdb-pair-rate-20261016")==0);
  assert(rrzc_real::Parse("original",&plan)&&plan.keys==524288);
  assert(plan.write_seconds==120);
  std::cout<<"真实数据库计划边界检查通过；原读写键范围不变。\n";
}
