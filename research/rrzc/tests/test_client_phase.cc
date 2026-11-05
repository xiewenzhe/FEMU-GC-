// 合成时间戳仅验证观测账目，不是性能实验。
#include "rrzc_client_phase.h"
#include <cassert>
#include <sstream>
#include <iostream>
int main() {
  rrzc_client_phase::Timer timer;std::ostringstream disabled;
  timer.Arm(false,1);timer.Print(disabled);assert(disabled.str().empty());
  timer.Arm(true,2);assert(!timer.Enabled() && !timer.Complete());
  timer.Arm(true,1);assert(timer.Enabled() && !timer.Complete());
  timer.Start(4,100);timer.RequestsDone(140);timer.CleanupBegin(145);timer.CleanupDone(165);
  assert(timer.Complete());timer.Print(std::cout);
  timer.CleanupDone(144);assert(!timer.Complete());
  timer.Arm(true,1);assert(!timer.Complete());
}
