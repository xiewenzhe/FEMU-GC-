// 只测旁观者生命周期及失败路径；合成记录不是设备实验结果。
#include "joint_read_trace.h"
#include <cassert>
#include <filesystem>
#include <iostream>

int main(int argc,char** argv) {
  assert(argc==2);const std::string root=argv[1];
  std::filesystem::create_directory(root+"/off");
  unsigned calls=0;
  {
    JointReadTrace off("0",root+"/off",[&]{++calls;return rrzc_v1::RecentReport{};});
    off.Close();assert(!off.Enabled());
  }
  assert(!calls && std::filesystem::is_empty(root+"/off"));
  std::filesystem::create_directory(root+"/good");
  {
    JointReadTrace trace("1",root+"/good",[&] {
      rrzc_v1::RecentReport r;r.threshold=1024;r.session_hi=1;r.blocks_per_zone=128;
      rrzc_v1::RecentBlock b;b.flags=7;b.snapshot_ns=100000000000ULL+calls++;
      r.zones.push_back(b);return r;
    },std::chrono::milliseconds(2));
    std::this_thread::sleep_for(std::chrono::milliseconds(25));trace.Close();trace.Close();
  }
  assert(calls && std::filesystem::exists(root+"/good/block-trace.status"));
  std::filesystem::create_directory(root+"/bad");
  bool failed=false;
  {
    JointReadTrace trace("1",root+"/bad",[]()->rrzc_v1::RecentReport {
      throw std::runtime_error("合成查询错误");
    },std::chrono::milliseconds(2));
    std::this_thread::sleep_for(std::chrono::milliseconds(25));
    try { trace.Close(); } catch (const std::runtime_error&) {failed=true;}
  }
  assert(failed);
  std::cout<<"只读旁观者默认关闭、线程收尾、完整记录及查询失败传播通过\n";
}
