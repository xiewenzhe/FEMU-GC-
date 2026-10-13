// 验证性单区成本比较内核，不是最终预测器或正式评分公式。
// 输入中的未来字节是估计值；内核不读取文件名、人工受害区或未来失效计划。
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <vector>

namespace rrzc {
struct Observation {
  uint64_t ns = 0, generation = 0, sequence = 0, valid = 0, maximum = 0;
  uint64_t total = 0, threshold = 0;
  bool stable = false;
};

struct Trend {
  Observation last;
  bool initialized = false, ready = false;
  double previous_rate = 0, rate = 0;

  void Observe(const Observation& now) {
    // 最大值可能来自不同物理块，所得斜率仅称为摘要趋势。
    const bool comparable = initialized && last.stable && now.stable &&
        now.generation == last.generation && now.sequence == last.sequence &&
        now.valid == last.valid && now.threshold == last.threshold &&
        now.maximum >= last.maximum && now.total >= last.total &&
        now.ns > last.ns && now.ns-last.ns <= 30000000000ULL;
    if (comparable) {
      const double current = double(now.maximum-last.maximum)*1e9/double(now.ns-last.ns);
      rate = std::max(current, previous_rate);
      previous_rate = current;
      ready = true;
    } else {
      ready = false; rate = previous_rate = 0;
    }
    last = now; initialized = true;
  }

  uint64_t Growth(uint64_t seconds) const {
    if (!ready || !std::isfinite(rate) || rate < 0) return 0;
    const long double value = std::ceil(static_cast<long double>(rate)*seconds);
    if (value >= std::numeric_limits<uint64_t>::max()) return std::numeric_limits<uint64_t>::max();
    return static_cast<uint64_t>(value);
  }
};

struct Candidate {
  uint64_t zone = 0, valid = 0, reclaimed = 0;
  uint64_t rr_if_skipped = 0, rr_after_selected = 0;
  bool covered = false, target_available = false, estimate_available = false;
};

struct Decision {
  bool found = false;
  Candidate selected;
  int64_t incremental_bytes = 0;
};

inline Decision ChooseOne(const std::vector<Candidate>& candidates,
                          uint64_t reclaim_budget, bool use_rr) {
  Decision result;
  if (reclaim_budget == 0) return result;
  for (const auto& c : candidates) {
    if (!c.covered || !c.target_available || c.valid == 0 || c.reclaimed < reclaim_budget) continue;
    // 比较相对“不选”的新增成本；其余候选的不选成本是公共项。
    // 同为字节的成本可直接比较，无读数与字节的任意加权。
    __int128 cost = c.valid;
    if (use_rr && c.estimate_available)
      cost += static_cast<__int128>(c.rr_after_selected)-c.rr_if_skipped;
    if (cost < std::numeric_limits<int64_t>::min() || cost > std::numeric_limits<int64_t>::max()) continue;
    const int64_t value = static_cast<int64_t>(cost);
    if (!result.found || value < result.incremental_bytes ||
        (value == result.incremental_bytes &&
         (c.reclaimed > result.selected.reclaimed ||
          (c.reclaimed == result.selected.reclaimed && c.zone < result.selected.zone)))) {
      result = {true, c, value};
    }
  }
  return result;
}
}  // 命名空间结束。
