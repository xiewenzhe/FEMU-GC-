// 只检查分类计数与衰减边界；夹具不作为收益数据。
#include "rrzc_stream_profile.h"
#include <cassert>
int main(){
  rrzc_stream::Profile sequential,hot;
  for(uint64_t i=0;i<256;++i)sequential.Read(i*4096,4096);
  assert(!sequential.Snapshot().hot);
  for(int i=0;i<256;++i)hot.Read(4096,4096);
  assert(hot.Snapshot().hot && hot.Snapshot().reused==255);
  for(uint64_t i=0;i<rrzc_stream::kWindow;++i)rrzc_stream::Tick();
  assert(!hot.Snapshot().hot && hot.Snapshot().reads==0);
  hot.Read(4096,4096);assert(!hot.Snapshot().hot);
  rrzc_stream::Profile cycles;
  for(int pass=0;pass<4;++pass)for(uint64_t page=0;page<1024;++page)
    cycles.Read(page*4096,4096,1024*4096);
  assert(cycles.Snapshot(1024*4096).hot && cycles.Snapshot().reused==0);
}
