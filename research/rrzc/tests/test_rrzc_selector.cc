// 合成输入仅核验选择内核，不是设备实验或性能数据。
#include "../model/rrzc_selector.h"
#include <iostream>
#include <stdexcept>
static unsigned checks = 0;
static void Check(bool condition) {
  ++checks;
  if (!condition) throw std::runtime_error("选择内核断言失败");
}
int main() {
  using namespace rrzc;
  Candidate cold{10, 64, 192, 0, 0, true, true, true};
  Candidate hot{20, 64, 192, 64, 0, true, true, true};
  Check(ChooseOne({cold, hot}, 192, false).selected.zone == 10);
  Check(ChooseOne({cold, hot}, 192, true).selected.zone == 20);
  Check(!ChooseOne({cold, hot}, 193, true).found);
  Check(!ChooseOne({cold, hot}, 0, true).found);
  hot.covered = false;
  Check(ChooseOne({cold, hot}, 192, true).selected.zone == 10);
  hot.covered = true; hot.target_available = false;
  Check(ChooseOne({cold, hot}, 192, true).selected.zone == 10);
  hot.target_available = true; hot.estimate_available = false;
  Check(ChooseOne({cold, hot}, 192, true).selected.zone == 10);
  hot.estimate_available = true; hot.rr_after_selected = 64;
  Check(ChooseOne({cold, hot}, 192, true).selected.zone == 10);
  hot.valid = 128; hot.rr_if_skipped = 32; hot.rr_after_selected = 0;
  Check(ChooseOne({cold, hot}, 192, true).selected.zone == 10);
  cold.reclaimed = 191;
  Check(ChooseOne({cold, hot}, 192, true).selected.zone == 20);
  hot.valid = 0;
  Check(!ChooseOne({cold, hot}, 192, true).found);
  hot.valid = UINT64_MAX; hot.rr_if_skipped = 0;
  Check(!ChooseOne({hot}, 192, true).found);
  Trend trend;
  Observation a{1000000000, 0, 0, 64, 100, 800, 4096, true};
  trend.Observe(a); Check(!trend.ready);
  a.ns += 10000000000; a.maximum += 320; a.total += 320;
  trend.Observe(a); Check(trend.ready && trend.Growth(40) == 1280);
  a.ns += 10000000000;
  trend.Observe(a); Check(trend.Growth(40) == 1280);
  a.ns += 10000000000;
  trend.Observe(a); Check(trend.Growth(40) == 0);
  a.generation++; a.sequence += 2; a.maximum = a.total = 0; a.ns += 10000000000;
  trend.Observe(a); Check(!trend.ready);
  a.ns += 10000000000; trend.Observe(a); Check(trend.ready);
  a.valid--; a.ns += 10000000000; trend.Observe(a); Check(!trend.ready);
  a.ns += 10000000000; a.stable = false; trend.Observe(a); Check(!trend.ready);
  a.ns += 10000000000; a.stable = true; trend.Observe(a); Check(!trend.ready);
  a.ns += 10000000000; trend.Observe(a); Check(trend.ready);
  a.ns += 31000000000; trend.Observe(a); Check(!trend.ready);
  a.ns += 10000000000; a.maximum = 1; a.total = 1; trend.Observe(a); Check(trend.ready);
  a.ns += 10000000000; a.maximum = 0; a.total = 0; trend.Observe(a); Check(!trend.ready);
  std::cout << "选择内核通过：" << checks << "项合成断言，未运行设备实验" << std::endl;
}
