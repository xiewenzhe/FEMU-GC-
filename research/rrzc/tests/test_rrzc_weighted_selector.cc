// 合成输入只用于单元测试，不是设备运行结果。
#include "../model/rrzc_weighted_selector.h"
#include <iostream>
#include <stdexcept>
unsigned checks = 0;
void Check(bool ok) { ++checks; if (!ok) throw std::runtime_error("双因素选择断言失败"); }
int main() {
  using namespace rrzc_simple;
  Candidate cold{10, 64, 256, 448, 4096, true, true, true};
  Candidate hot{20, 64, 256, 3584, 4096, true, true, true};
  Check(Choose({cold, hot}, 192, false).selected.zone == 10);
  auto d = Choose({cold, hot}, 192, true);
  Check(d.selected.zone == 20 && d.used_weighted);
  Check(d.space_ppm == 750000 && d.risk_ppm == 875000 && d.score_ppm == 812500);
  hot.valid = 240; hot.maximum_reads = 4096; cold.maximum_reads = 2048;
  Check(Choose({cold, hot}, 16, true).selected.zone == 10);
  hot.valid = 128; hot.maximum_reads = 3584; cold.maximum_reads = 448;
  Check(Choose({cold, hot}, 128, true).selected.zone == 20);
  Check(Choose({cold, hot}, 128, false).selected.zone == 10);
  Check(Choose({cold, hot}, 192, true).selected.zone == 10);
  Check(!Choose({cold, hot}, 193, true).found);
  Check(!Choose({cold, hot}, 0, true).found);
  hot.valid = 64; hot.pressure_available = false;
  d = Choose({cold, hot}, 192, true);
  Check(d.selected.zone == 10 && !d.used_weighted);
  hot.pressure_available = true; hot.threshold = 0;
  Check(!Choose({cold, hot}, 192, true).used_weighted);
  hot.threshold = 4096; hot.maximum_reads = UINT64_MAX;
  Check(Choose({cold, hot}, 192, true).risk_ppm == 1000000);
  hot.covered = false;
  Check(Choose({cold, hot}, 192, true).selected.zone == 10);
  hot.covered = true; hot.target_available = false;
  Check(Choose({cold, hot}, 192, true).selected.zone == 10);
  hot.target_available = true; hot.valid = 257;
  Check(Choose({cold, hot}, 192, true).selected.zone == 10);
  hot.valid = 0;
  Check(!Choose({hot}, 192, true).found);
  hot = cold; hot.zone = 20;
  Check(Choose({hot, cold}, 192, true).selected.zone == 10);
  Candidate huge{30, 1, UINT64_MAX, UINT64_MAX, UINT64_MAX, true, true, true};
  Check(Choose({huge}, UINT64_MAX-1, true).score_ppm <= kScale);
  std::cout << "双因素内核通过：" << checks << "项合成断言" << std::endl;
}
