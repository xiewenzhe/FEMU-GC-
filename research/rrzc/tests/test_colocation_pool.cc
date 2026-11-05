// 验证资源、Reset和失效建议边界；合成Zone观测不是设备实验或收益结果。
#include "rrzc_colocation_pool.h"
#include <cassert>
#include <iostream>
#include <thread>
using namespace rrzc_colocation;
static Zone Z(uint64_t id,State state=State::kEmpty) {
  return {id,1,128,state,false,false,true,true};
}
int main() {
  Resources resources{10,10,5};std::vector<Zone> zones{Z(7),Z(9)};
  Pool disabled;
  assert(disabled.Choose({}, {},Tag::kReadHot,1).action==Action::kFallback);
  Pool pool(1);
  auto first=pool.Choose(zones,resources,Tag::kReadHot,20);
  assert(first.action==Action::kCreate && first.zone==7 && pool.Commit(first));
  zones[0].state=State::kClosed;zones[0].remaining=108;
  auto mixed=pool.Choose(zones,resources,Tag::kShortLived,80);
  assert(mixed.action==Action::kReuse && mixed.zone==7 && pool.Commit(mixed));
  assert(pool.Snapshot().front().observed_tags==3);
  zones[0].busy=true;
  assert(pool.Choose(zones,resources,Tag::kReadHot,1).action==Action::kFallback);
  zones[0].busy=false;
  assert(pool.Choose(zones,resources,Tag::kReadHot,1,{7}).action==Action::kFallback);
  zones[0].risk_safe=false;
  assert(pool.Choose(zones,resources,Tag::kReadHot,1).action==Action::kFallback);
  zones[0].risk_safe=true;
  // Closed仍占K；Full释放可写名额，但保留成员历史。
  zones[0].state=State::kFull;zones[0].remaining=0;
  auto next=pool.Choose(zones,resources,Tag::kReadHot,1);
  assert(next.action==Action::kCreate && next.zone==9);
  // 同一份建议并发提交，最多一方成功；第二方必须重新获取原生资源与建议。
  bool a=false,b=false;
  std::thread one([&]{a=pool.Commit(next);}),two([&]{b=pool.Commit(next);});
  one.join();two.join();assert(a!=b);
  zones[1].state=State::kClosed;
  pool.SetLimit(0);
  assert(pool.Contains(9) && pool.Choose(zones,resources,Tag::kReadHot,1).action==Action::kFallback);
  auto saved=pool.Snapshot();Pool restored(1);restored.Restore(saved,zones);
  assert(restored.Snapshot().size()==2);
  zones[1].generation=2;zones[1].state=State::kEmpty;
  restored.Restore(saved,zones);assert(!restored.Contains(9));
  restored.Reset(7);assert(restored.Snapshot().empty());
  resources.free_active=0;
  assert(restored.Choose(zones,resources,Tag::kReadHot,1).action==Action::kFallback);
  resources.free_active=10;resources.free_open=0;
  assert(restored.Choose(zones,resources,Tag::kReadHot,1).action==Action::kFallback);
  resources.free_open=5;
  assert(restored.Choose(zones,resources,Tag::kNormal,1).action==Action::kFallback);
  bool rejected=false;
  try{restored.Restore({saved[0],saved[0]},zones);}catch(const std::invalid_argument&){rejected=true;}
  assert(rejected);
  std::cout<<"协同池独立策略核检查通过；尚未代表ZenFS接线或真实重挂载验证\n";
}
