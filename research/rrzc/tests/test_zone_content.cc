// 合成分类边界，不是设备隔离或性能测试。
#include "rrzc_zone_content.h"
#include <cassert>
int main() {
  using namespace rrzc_content;
  assert(!Enabled(nullptr) && !Enabled("0") && Enabled("1"));
  assert(Classify("/a/000008.sst")==kSst);
  for(const auto* n:{"CURRENT","OPTIONS-000007","MANIFEST-000005","/a/file.sst.tmp","x",""})
    assert(Classify(n)==kOther);
  for(uint8_t old=0;old<4;++old)for(uint8_t next=0;next<4;++next) {
    assert(Compatible(old,next)==((next==1 || next==2) && old==next));
    assert(Claimable(old,next)==((next==1 || next==2) && (old==0 || old==next)));
  }
}
