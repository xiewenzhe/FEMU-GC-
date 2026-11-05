// 严格RR等待：只在读或一次维护期间持锁；等待SST关闭时允许无关读取推进。
#pragma once
#include <chrono>
#include <thread>
#include "rrzc_strict_io.h"

namespace rrzc_strict {
template<class Read, class Service, class Lock, class Timeout>
auto ReadWithMaintenance(Read read, Service service, Lock lock, Timeout timeout,
                         std::chrono::milliseconds limit=std::chrono::seconds(30))
    -> decltype(read()) {
  const auto deadline=std::chrono::steady_clock::now()+limit;
  bool pending=false;
  auto delay=std::chrono::milliseconds(1);
  for (;;) {
    {
      auto guard=lock();
      if (!pending) {
        reached=false;
        const auto status=read();
        if (!reached) return status;
        pending=true;
      }
      // 到阈值的当前请求只有维护完成后才能重读；不借重试越过设备阈值。
      bool complete=false;
      const auto status=service(&complete);
      if (!status.ok()) return status;
      pending=!complete;
    }
    // 必须在释放全局锁后等待，否则压实无法读取并关闭快照漏掉的SST。
    if (std::chrono::steady_clock::now()>=deadline) return timeout();
    if (pending) {
      std::this_thread::sleep_for(delay);
      if (delay<std::chrono::milliseconds(50)) {
        delay*=2;
        if (delay>std::chrono::milliseconds(50)) delay=std::chrono::milliseconds(50);
      }
    }
  }
}
}
