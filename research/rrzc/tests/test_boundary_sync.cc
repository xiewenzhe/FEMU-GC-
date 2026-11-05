// 合成交接测试不代表FEMU实验结果；验证一一确认、错误确认拒绝和有界等待。
#include "rrzc_boundary_sync.h"
#include <atomic>
#include <cassert>
#include <thread>

int main() {
  rrzc_boundary::Space protocol;
  std::atomic<unsigned> actual{0};
  std::thread maintenance([&] {
    for(unsigned i=1;i<=1000;++i) {
      uint64_t request=0;
      while(!(request=protocol.Pending()))std::this_thread::yield();
      assert(request==i);
      assert(!protocol.Complete(request+1));
      actual.store(i);
      assert(protocol.Complete(request));
      assert(!protocol.Complete(request));
    }
  });
  for(unsigned i=1;i<=1000;++i) {
    const auto request=protocol.Request();assert(request==i);
    assert(protocol.Wait(request,std::chrono::steady_clock::now()+std::chrono::seconds(5)));
    assert(actual.load()==i);
  }
  maintenance.join();assert(protocol.Pending()==0);
  rrzc_boundary::Space timeout;
  assert(timeout.Request()==1 && timeout.Request()==0);
  assert(!timeout.Wait(1,std::chrono::steady_clock::now()+std::chrono::milliseconds(2)));
  assert(timeout.Complete(1));
}
