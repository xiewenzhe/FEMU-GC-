// 合成边界及线程闭环测试；不是实际设备实验结果。
#include "rrzc_space_emergency.h"
#include <cassert>
#include <condition_variable>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <thread>

int main() {
  constexpr uint64_t m=1ULL<<20;
  rrzc_simple::Candidate non_sst,sst,other;
  sst.zone=5637144576;sst.valid=115198278;sst.capacity=256*m;
  sst.covered=sst.target_available=true;
  non_sst=sst;non_sst.zone=1342177280;non_sst.valid=7852;non_sst.covered=false;
  other=sst;other.zone=7516192768;other.valid=116507027;
  assert(!rrzc_space::Enabled(nullptr,true));
  assert(!rrzc_space::Enabled("1",false));
  assert(rrzc_space::Enabled("1",true));
  assert(!rrzc_space::Choose({sst},false,1,false).found);
  assert(!rrzc_space::Choose({sst},true,0,false).found);
  assert(!rrzc_space::Choose({sst},true,1,true).found);
  assert(rrzc_space::Choose({non_sst,other,sst},true,1,false).selected.zone==sst.zone);
  other=sst;other.target_available=false;
  assert(!rrzc_space::Choose({non_sst,other},true,1,false).found);
  other=sst;other.valid=other.capacity;
  assert(!rrzc_space::Choose({other},true,1,false).found);
  other=sst;other.zone=sst.zone-1;
  assert(rrzc_space::Choose({sst,other},true,1,false).selected.zone==other.zone);
  rrzc_space::Pressure pressure;
  try { rrzc_space::Pressure::Wait wait(pressure);assert(pressure.Count()==1);throw std::runtime_error("合成错误"); }
  catch (const std::runtime_error&) {}
  assert(pressure.Count()==0);
  // 合成模拟：真实等待作用域→选择→模拟迁移/Reset→分配恢复→撤销等待。
  std::mutex mutex;std::condition_variable cv;bool waiting=false,empty=false,done=false;
  std::thread allocator([&] {
    rrzc_space::Pressure::Wait wait(pressure);
    std::unique_lock<std::mutex> lock(mutex);waiting=true;cv.notify_all();
    cv.wait(lock,[&]{return empty;});done=true;
  });
  {
    std::unique_lock<std::mutex> lock(mutex);cv.wait(lock,[&]{return waiting;});
    auto choice=rrzc_space::Choose({non_sst,sst},true,pressure.Count(),false);
    assert(choice.found && choice.selected.zone==sst.zone);
    assert(choice.selected.valid<256*m);empty=true;cv.notify_all();
  }
  allocator.join();assert(done && pressure.Count()==0);
  std::cout<<"空间紧急恢复边界及合成闭环通过\n";
}
