// 固定条带请求计划的内存验证，不是物理布局或实验收益证据。
#include "rocksdb_observe_plan.h"
#include <cassert>
#include <set>
#include <iostream>
int main() {
  using namespace rrzc_anchor;
  rrzc_real::Plan p;assert(rrzc_real::Parse("anchors90",&p)&&p.anchors&&p.write_seconds==90&&p.keys==655360);
  std::set<unsigned> writes,anchors;
  for(unsigned i=0;i<WritableKeys;++i) {
    unsigned k=WriteKey(i);assert(k<262144&&Ordinal(k)==i);writes.insert(k);
  }
  assert(writes.size()==229376&&WriteKey(WritableKeys)==0);
  unsigned holes=0;
  for(unsigned k=0;k<262144;++k) {
    if(Ordinal(k)==NoOrdinal) {++holes;assert(!writes.count(k));}
    else assert(WriteKey(Ordinal(k))==k);
  }
  assert(holes==32768&&Ordinal(262144)==NoOrdinal);
  unsigned anchor_reads=0;
  for(unsigned i=0;i<48000;++i) {
    unsigned k=ReadKey(i);assert(k<262144);
    if(Ordinal(k)==NoOrdinal) {assert(i>=18000&&i%2==1);++anchor_reads;anchors.insert(k);}
    else assert(k<=15360);
  }
  assert(anchor_reads==15000&&anchors==std::set<unsigned>({36864,102400,167936,233472}));
  assert(p.write_rate==8192&&p.write_seconds*p.write_rate==737280);
  assert(rrzc_real::Parse("anchors180slow",&p)&&p.anchors&&p.keys==655360);
  assert(p.write_seconds==180&&p.write_rate==4096&&p.write_seconds*p.write_rate==737280);
  assert(std::strcmp(p.label,"rocksdb-anchor-slow-20261016")==0);
  assert(uint64_t(64)*1000000000/p.write_rate==15625000);
  // 全部更新序号仍使用同一映射；半速计划不更新锚点，也不改变最终版本。
  for(unsigned i=0;i<p.write_seconds*p.write_rate;++i) assert(Ordinal(WriteKey(i))==i%WritableKeys);
  assert(rrzc_real::Parse("pair90",&p)&&!p.anchors);
  assert(p.write_rate==8192&&p.write_seconds==90);
  assert(rrzc_real::Parse(nullptr,&p)&&!p.anchors&&p.write_seconds==120&&p.write_rate==8192);
  std::cout<<"条带锚点：229376个可写键、32768个保留键、完整映射与48000次读取计划检查通过。\n";
}
