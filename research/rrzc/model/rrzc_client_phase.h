// 客户端观测专用：显式开关、仅单线程；不参与请求生成或设备维护决策。
#pragma once
#include <chrono>
#include <cstdint>
#include <ostream>

namespace rrzc_client_phase {
inline uint64_t Now() {
  return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count());
}
class Timer {
 public:
  void Arm(bool requested,uint64_t threads) {
    requested_=requested;threads_=threads;ops_=start_=requests_=close_start_=end_=0;
  }
  bool Enabled() const {return requested_ && threads_==1;}
  void Start(uint64_t ops,uint64_t now) {ops_=ops;start_=now;}
  void RequestsDone(uint64_t now) {requests_=now;}
  void CleanupBegin(uint64_t now) {close_start_=now;}
  void CleanupDone(uint64_t now) {end_=now;}
  bool Complete() const {
    return Enabled() && ops_ && start_ && start_<=requests_ && requests_<=close_start_ && close_start_<=end_;
  }
  void Print(std::ostream& out) const {
    if(!requested_)return;
    out<<"客户端阶段计时：版本=1，线程数="<<threads_<<"，操作数="<<ops_<<"，完整="<<Complete();
    if(Complete())out<<"，前台纳秒="<<requests_-start_<<"，Cleanup纳秒="<<end_-close_start_
        <<"，间隙纳秒="<<close_start_-requests_<<"，工作线程总纳秒="<<end_-start_
        <<"，前台开始="<<start_<<"，前台结束="<<requests_<<"，Cleanup开始="<<close_start_<<"，Cleanup结束="<<end_;
    out<<'\n';
  }
 private:
  bool requested_=false;
  uint64_t threads_=0,ops_=0,start_=0,requests_=0,close_start_=0,end_=0;
};
// 主线程在启动唯一工作线程前设置，future.get之后读取；不在每次API调用计时。
inline Timer& Global() {static Timer timer;return timer;}
}  // 客户端阶段观测命名空间结束。
