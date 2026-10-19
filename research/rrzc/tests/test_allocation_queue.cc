// 合成并发验证顺序和作用域释放，不代表设备性能结果。
#include "rrzc_allocation_queue.h"
#include <cassert>
#include <memory>
#include <thread>
#include <vector>
#include <iostream>
int main() {
  rrzc_fifo::Queue q;
  auto first=std::make_unique<rrzc_fifo::Queue::Turn>(q);
  assert(first->Number()==0);
  std::vector<uint64_t> order;
  std::thread a([&]{rrzc_fifo::Queue::Turn t(q);order.push_back(t.Number());});
  while(q.Issued()<2) std::this_thread::yield();
  std::thread b([&]{rrzc_fifo::Queue::Turn t(q);order.push_back(t.Number());});
  while(q.Issued()<3) std::this_thread::yield();
  first.reset();a.join();b.join();
  assert((order==std::vector<uint64_t>{1,2}));
  {rrzc_fifo::Queue::Turn t(q);assert(t.Number()==3);}
  {rrzc_fifo::Queue::Turn t(q);assert(t.Number()==4);}
  std::cout<<"普通Zone分配先进先出及作用域释放检查通过\n";
}
