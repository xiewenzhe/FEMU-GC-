// 内存并发测试，不访问设备；等待仅用于同步后台回调，不模拟性能结果。
#include "rrzc_recent_sampler.h"
#include <cassert>
#include <iostream>

int main() {
  using namespace rrzc_v1;
  assert(!IndependentSamplingEnabled(nullptr,true,false));
  assert(!IndependentSamplingEnabled("0",true,false));
  assert(!IndependentSamplingEnabled("1",false,false));
  assert(!IndependentSamplingEnabled("1",true,true));
  assert(IndependentSamplingEnabled("1",true,false));
  std::mutex mutex; std::condition_variable wake;
  unsigned calls=0,permitted=0;
  bool failed=false;
  RecentReport report; report.threshold=4096; report.blocks_per_zone=64;
  report.session_hi=1;
  RecentBlock block; block.zone=256; block.flags=kEnabled;
  block.snapshot_ns=10*kSecond; block.rc=100; report.zones={block};
  RecentSampler sampler([&](RecentReport* out) {
    std::unique_lock<std::mutex> lock(mutex);
    ++calls; wake.notify_all();
    wake.wait(lock,[&]{return permitted>=calls;});
    *out=report; return !failed;
  },std::chrono::milliseconds(5));
  auto wait_call=[&](unsigned count) {
    std::unique_lock<std::mutex> lock(mutex);
    assert(wake.wait_for(lock,std::chrono::seconds(2),[&]{return calls>=count;}));
  };
  auto release=[&](unsigned count) {
    { std::lock_guard<std::mutex> lock(mutex); permitted=count; }
    wake.notify_all(); wait_call(count+1);
  };
  sampler.Start(); wait_call(1);
  assert(sampler.Freeze().sequence==0);
  release(1);
  auto frozen=sampler.Freeze(); assert(frozen.sequence==1);
  block.snapshot_ns+=kSecond; block.rc+=100;
  auto current=report; current.zones={block};
  auto evidence=frozen.history.Check(current,block,10*kSecond);
  assert(evidence.known&&evidence.delta==100&&evidence.interval_ns==kSecond);
  // 主线程不再采样时后台仍发布；冻结副本不会被后续查询覆盖。
  { std::lock_guard<std::mutex> lock(mutex); report=current; }
  release(2);
  assert(sampler.Freeze().sequence==2&&frozen.sequence==1);
  assert(frozen.history.Check(current,block,10*kSecond).known);
  assert(!sampler.Freeze().history.Check(current,block,10*kSecond).known);
  auto changed=block; ++changed.generation;
  assert(!frozen.history.Check(current,changed,10*kSecond).known);
  changed=block; ++changed.block_id;
  assert(!frozen.history.Check(current,changed,10*kSecond).known);
  sampler.Clear(); assert(!sampler.Freeze().history.Check(current,block,10*kSecond).has_previous);
  { std::lock_guard<std::mutex> lock(mutex); failed=true; }
  release(3);
  assert(!sampler.Freeze().history.Check(current,block,10*kSecond).has_previous);
  { std::lock_guard<std::mutex> lock(mutex); failed=false; ++report.session_hi; }
  release(4);
  assert(!sampler.Freeze().history.Check(current,block,10*kSecond).known);
  // 停止必须等待在途查询返回，禁止先销毁查询依赖的设备。
  std::thread stopping([&]{sampler.Stop();});
  { std::lock_guard<std::mutex> lock(mutex); permitted=1000000; }
  wake.notify_all(); stopping.join(); sampler.Stop();
  std::cout<<"独立采样：默认关闭、冻结、并发发布、失败、换块、代际及退出检查通过。\n";
}
