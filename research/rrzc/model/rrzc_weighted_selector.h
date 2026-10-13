// 简单双因素版本：有效数据与区内最大块读数各占50%，不预测未来。
#pragma once
#include <algorithm>
#include <cstdint>
#include <vector>

namespace rrzc_simple {
constexpr uint64_t kScale = 1000000;
struct Candidate {
  uint64_t zone = 0, valid = 0, capacity = 0, maximum_reads = 0, threshold = 0;
  bool covered = false, target_available = false, pressure_available = false;
};
struct Decision {
  bool found = false, used_weighted = false;
  Candidate selected;
  uint64_t space_ppm = 0, risk_ppm = 0, score_ppm = 0;
};
inline uint64_t Ratio(uint64_t value, uint64_t capacity) {
  return static_cast<uint64_t>(static_cast<__uint128_t>(value)*kScale/capacity);
}
inline bool Eligible(const Candidate& c, uint64_t budget) {
  return budget > 0 && c.capacity > 0 && c.valid > 0 && c.valid <= c.capacity &&
         c.covered && c.target_available && c.capacity-c.valid >= budget;
}
inline Decision Choose(const std::vector<Candidate>& candidates, uint64_t budget, bool weighted) {
  Decision result;
  bool complete_pressure = true;
  for (const auto& c : candidates)
    if (Eligible(c, budget) && (!c.pressure_available || c.threshold == 0)) complete_pressure = false;
  const bool use_weighted = weighted && complete_pressure;
  for (const auto& c : candidates) {
    if (!Eligible(c, budget)) continue;
    const uint64_t space = Ratio(c.capacity-c.valid, c.capacity);
    const uint64_t risk = c.pressure_available && c.threshold ?
        Ratio(std::min(c.maximum_reads, c.threshold), c.threshold) : 0;
    // 分数越大越优先；两个归一化分量相加再除2，即各50%。
    const uint64_t score = (space+risk)/2;
    const bool better = !result.found ||
        (use_weighted && score > result.score_ppm) ||
        ((!use_weighted || score == result.score_ppm) &&
         (c.valid < result.selected.valid ||
          (c.valid == result.selected.valid && c.zone < result.selected.zone)));
    if (better) result = {true, use_weighted, c, space, risk, score};
  }
  return result;
}
}  // 命名空间结束。
