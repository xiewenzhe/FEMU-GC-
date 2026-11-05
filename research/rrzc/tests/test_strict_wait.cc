// 并发回归：等待开放SST时释放全局锁，当前到期读取仍须等维护完成。
#include "rrzc_strict_wait.h"
#include <atomic>
#include <cassert>
#include <mutex>
#include <iostream>

struct Status {
  int code;
  bool ok() const {return code==0;}
  bool IsTryAgain() const {return code==1;}
};
int main() {
  using namespace std::chrono_literals;
  std::recursive_mutex mutex;
  std::atomic<bool> waiting{false},closed{false};
  unsigned reads=0,attempts=0;
  std::thread writer([&]() {
    while (!waiting.load()) std::this_thread::yield();
    auto guard=std::unique_lock<std::recursive_mutex>(mutex);
    // 模拟压实读取另一块后关闭正在写入的SST。
    closed=true;
  });
  const auto status=rrzc_strict::ReadWithMaintenance(
      [&]() {++reads;rrzc_strict::reached=reads==1;return Status{0};},
      [&](bool* complete) {++attempts;waiting=true;*complete=closed;return Status{0};},
      [&]() {return std::unique_lock<std::recursive_mutex>(mutex);},
      []() {return Status{2};},500ms);
  writer.join();
  assert(status.ok() && closed && reads==2 && attempts>=2);
  reads=0;
  const auto timeout=rrzc_strict::ReadWithMaintenance(
      [&]() {++reads;rrzc_strict::reached=true;return Status{0};},
      [](bool* complete) {*complete=false;return Status{0};},
      [&]() {return std::unique_lock<std::recursive_mutex>(mutex);},
      []() {return Status{2};},10ms);
  assert(timeout.code==2 && reads==1); // 覆盖始终不全时不能再次普通读。
  const auto failure=rrzc_strict::ReadWithMaintenance(
      []() {rrzc_strict::reached=true;return Status{0};},
      [](bool*) {return Status{3};},
      [&]() {return std::unique_lock<std::recursive_mutex>(mutex);},
      []() {return Status{2};});
  assert(failure.code==3);
  std::cout<<"严格RR并发等待通过：无关读取可推进，当前请求不越阈值，超时及真实错误拒绝继续\n";
}
