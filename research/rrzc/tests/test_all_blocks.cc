// 合成帧验证因果边界；不把内存模型输出当作真实设备收益。
#include "rrzc_all_blocks.h"
#include <cassert>
#include <iostream>
using namespace rrzc_all_blocks;
static Report Frame(uint64_t seconds,uint64_t a,uint64_t b) {
  Report r;r.threshold=1024;r.session_hi=7;r.nr_zones=64;r.blocks_per_zone=2;
  for(unsigned id=0;id<2;++id) {
    RecentBlock x;x.zone=5*(128ULL<<20);x.zone_index=5;x.block_id=id;x.ch=id;
    x.snapshot_ns=seconds*kSecond;x.rc=id?b:a;x.delta10=id?400:10;
    x.delta30=id?400:980;x.flags=7;r.blocks.push_back(x);
  }
  return r;
}
static void Put(uint8_t* p,uint64_t v,unsigned n) {
  for(unsigned i=0;i<n;++i)p[i]=v>>(8*i);
}
static std::vector<uint8_t> Wire(const Report& r) {
  std::vector<uint8_t> bytes(56+88*r.blocks.size());auto* p=bytes.data();
  Put(p,1,4);Put(p+4,88,4);Put(p+8,r.nr_zones,4);Put(p+12,r.blocks_per_zone,4);
  Put(p+16,r.threshold,8);Put(p+24,r.session_hi,8);Put(p+32,r.session_lo,8);
  Put(p+40,10*kSecond,8);Put(p+48,30*kSecond,8);
  for(const auto& b:r.blocks) {
    const auto offset=size_t(&b-r.blocks.data());p=bytes.data()+56+88*offset;
    const uint64_t words[]={b.zone/512,b.generation,b.snapshot_ns,b.epoch_start_ns,b.rc,b.delta10,b.delta30};
    for(unsigned i=0;i<7;++i)Put(p+8*i,words[i],8);
    const uint32_t small[]={b.zone_index,b.block_id,b.ch,b.lun,b.plane,b.block,b.flags,0};
    for(unsigned i=0;i<8;++i)Put(p+56+4*i,small[i],4);
  }
  return bytes;
}
int main() {
  const auto current=Frame(40,980,500);History h;
  h.Push(Frame(37,977,380));h.Push(Frame(38,978,420));h.Push(Frame(39,979,460));
  rrzc_simple::Candidate c{current.blocks[0].zone,5ULL<<20,128ULL<<20,980,1024,true,true,true};
  rrzc_window_credit::Retention f;f.known=true;f.zone=c.zone;f.valid=c.valid;
  f.short_span=10*kSecond;f.long_span=30*kSecond;
  Evidence e;auto cost=Estimate(c,&current,&h,&f,10*kSecond,&e);
  assert(e.selected && e.complete && e.block.block_id==1 && e.known==2);
  assert(cost.eta==13100000000ULL);
  auto urgent=current;urgent.blocks[1].rc=900;
  urgent.blocks[1].delta10=urgent.blocks[1].delta30=800;
  cost=Estimate(c,&urgent,&h,&f,10*kSecond,&e);
  assert(e.block.block_id==1 && cost.credit>0 && cost.credit<=c.valid);
  // 最大RC块变更不影响同一个块的增长历史。
  auto switched=current;switched.blocks[1].rc=990;switched.blocks[1].delta10=890;
  assert(h.Check(switched,switched.blocks[1],10*kSecond).known);
  // 换代际、会话、PPA、时间倒退或缺帧，都必须拒绝跨界差分。
  for(unsigned mode=0;mode<5;++mode) {
    auto changed=current;
    if(mode==0)++changed.blocks[1].generation;
    if(mode==1)++changed.session_hi;
    if(mode==2)++changed.blocks[1].ch;
    if(mode==3)changed.blocks[1].snapshot_ns=38*kSecond;
    if(mode==4)changed.blocks[1].rc=1;
    assert(!h.Check(changed,changed.blocks[1],10*kSecond).known);
  }
  auto cold=current;for(auto& b:cold.blocks)b.delta10=0;
  cost=Estimate(c,&cold,&h,&f,10*kSecond,&e);assert(!cost.credit);
  auto invalid=current;invalid.blocks[1].flags=rrzc_v1::kEnabled|rrzc_v1::kLoss;
  cost=Estimate(c,&invalid,&h,&f,10*kSecond,&e);assert(!e.complete && e.known==1);
  // 两个到期块只抵扣一次整个Zone的有效量。
  auto due=current;for(auto& b:due.blocks)b.rc=1024;
  cost=Estimate(c,&due,&h,&f,10*kSecond,&e);assert(cost.credit==c.valid);
  auto bytes=Wire(current);Report decoded;std::string error;
  auto decode=[&]{return Decode(bytes.data(),bytes.size(),64,2,5,128ULL<<20,512,&decoded,&error);};
  assert(decode() && decoded.blocks.size()==2 && decoded.blocks[1].rc==500);
  // 防止别的Zone、重复块、混代际、非单帧、错误窗口或错误长度进入模型。
  for(const auto offset:{size_t(0),size_t(56),size_t(56+88+8),size_t(56+88+16),
                         size_t(56+88+60),size_t(40),size_t(56+84)}) {
    const auto old=bytes[offset];bytes[offset]^=1;assert(!decode() && decoded.blocks.empty());bytes[offset]=old;
  }
  bytes.pop_back();assert(!decode());
  Store store;store.Publish({current});assert(store.Freeze().size()==1);
  store.Publish({});assert(store.Freeze().empty());
  std::cout<<"全块协议、低RC快读、换最大块、Reset代际、窗口缺失及整Zone只计一次收益通过\n";
  return 0;
}
