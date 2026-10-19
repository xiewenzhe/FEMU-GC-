// 合成令牌竞争回归；真实设备资源与数据另做完整实验验证。
#include "rrzc_alloc_retry.h"
#include <cassert>
#include <iostream>
struct Status { bool good=true;static Status OK(){return {};}bool ok()const{return good;} };
int main() {
  assert(rrzc_alloc::Headroom(256ULL<<20)==128ULL<<20);
  assert(rrzc_alloc::Enough(176ULL<<20,256ULL<<20));
  assert(!rrzc_alloc::Enough(96ULL<<20,256ULL<<20));
  assert(rrzc_alloc::kWaitAttempts==1200);
  int acquire=0,finish=0;bool active=false;
  auto status=rrzc_alloc::ActivateClaimedEmpty<Status>([&]{return ++acquire==2;},
      [&]{++finish;return Status::OK();},&active);
  assert(status.ok() && active && acquire==2 && finish==1);
  acquire=finish=0;
  status=rrzc_alloc::ActivateClaimedEmpty<Status>([&]{++acquire;return true;},
      [&]{++finish;return Status::OK();},&active);
  assert(status.ok() && active && acquire==1 && finish==0);
  acquire=finish=0;
  status=rrzc_alloc::ActivateClaimedEmpty<Status>([&]{++acquire;return false;},
      [&]{++finish;return Status{false};},&active);
  assert(!status.ok() && !active && acquire==1 && finish==1);
  acquire=finish=0;
  status=rrzc_alloc::ActivateClaimedEmpty<Status>([&]{++acquire;return false;},
      [&]{++finish;return Status::OK();},&active);
  assert(status.ok() && !active && acquire==2 && finish==1);
  std::cout<<"空目标独占后活跃令牌重试边界检查通过\n";
}
