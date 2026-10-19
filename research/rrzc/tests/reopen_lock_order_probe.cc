// 仅内存诊断：真实ZoneFile开写锁配合模拟文件表锁，核验已发现的相反锁序。
// 不构造块设备、不挂载ZenFS、不读取或修改NVMe；不是原死锁现场线程栈。
#include <atomic>
#include <chrono>
#include <future>
#include <iostream>
#include <mutex>
#include <thread>
#include "plugin/zenfs/fs/io_zenfs.h"

int main() {
  using namespace rocksdb;
  using namespace std::chrono_literals;
  // 真实构造函数不访问设备；无片段的析构也不需要设备，源码已核对。
  ZoneFile file(nullptr, 1, nullptr);
  std::mutex table;
  std::promise<void> table_acquired;
  auto ready=table_acquired.get_future();
  std::atomic<bool> reopened{false};
  if (!file.TryAcquireWRLock()) return 2;
  // 主线程模拟迁移：先持文件开写锁，随后需要文件表锁。
  std::thread writer([&] {
    std::lock_guard<std::mutex> lock(table);
    table_acquired.set_value();
    // 与原OpenWritableFile重开分支一致：持表锁阻塞等文件开写锁。
    file.AcquireWRLock();
    reopened.store(true);
    file.ReleaseWRLock();
  });
  const bool held=ready.wait_for(2s)==std::future_status::ready;
  const bool table_available=table.try_lock();
  if (table_available) table.unlock();
  const bool still_waiting=!reopened.load();
  // 不执行会永久互等的第二次阻塞；先记录闭环条件，再主动释放以退出诊断。
  file.ReleaseWRLock();
  writer.join();
  const bool cycle=held && !table_available && still_waiting;
  std::cout<<"真实文件锁由迁移侧持有=1\n"
           <<"重开侧已持模拟文件表锁="<<held<<'\n'
           <<"迁移侧无法取得模拟文件表锁="<<!table_available<<'\n'
           <<"释放文件锁前重开尚未返回="<<still_waiting<<'\n'
           <<"释放文件锁后重开完成="<<reopened.load()<<'\n'
           <<"相反锁序闭环条件成立="<<cycle<<'\n'
           <<"说明=仅内存同步验证，不代表取得原进程线程栈或重跑SSD实验\n";
  return cycle && reopened.load() ? 0 : 1;
}
