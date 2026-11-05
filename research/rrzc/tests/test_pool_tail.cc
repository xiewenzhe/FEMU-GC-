// 验证有效数据、读风险、写入对齐和资源退出边界；不是收益夹具。
#include "rrzc_pool_tail.h"
#include <cassert>
int main(){
  rrzc_pool_tail::Input good;
  good.member=good.stable=good.risk_known=good.reserve_ready=true;
  good.remaining=4096;good.alignment=4096;good.rc=100;good.threshold=1024;good.active=8;good.limit=9;
  assert(rrzc_pool_tail::Keep(good));
  auto x=good;x.used=true;assert(!rrzc_pool_tail::Keep(x));
  x=good;x.remaining=4095;assert(!rrzc_pool_tail::Keep(x));
  x=good;x.rc=1024;assert(!rrzc_pool_tail::Keep(x));
  x=good;x.risk_known=false;assert(!rrzc_pool_tail::Keep(x));
  x=good;x.active=9;assert(!rrzc_pool_tail::Keep(x));
  x=good;x.waiters=1;assert(!rrzc_pool_tail::Keep(x));
  x=good;x.migrating=true;assert(!rrzc_pool_tail::Keep(x));
  x=good;x.reserve_ready=false;assert(!rrzc_pool_tail::Keep(x));
}
