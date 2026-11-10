// 文件来源、读热与寿命证据相互独立；本接口不预测寿命，不决定放置目标。
#pragma once
#include <cstdint>

namespace rrzc_file_characteristics {
enum class Origin : uint8_t { kUnknown, kFlush };
enum class Lifetime : uint8_t { kUnknown };

class Observed {
 public:
  constexpr Observed(bool flush_origin, bool read_hot)
      : origin_(flush_origin ? Origin::kFlush : Origin::kUnknown), hot_(read_hot) {}

  constexpr Origin Source() const { return origin_; }
  constexpr bool FlushOrigin() const { return origin_ == Origin::kFlush; }
  constexpr bool ReadHot() const { return hot_; }
  // 当前没有经过验证的在线寿命预测器。F、H、两者并存都不升级寿命证据。
  constexpr Lifetime LifetimeStatus() const { return Lifetime::kUnknown; }
  constexpr bool HasShortLifetimeEvidence() const { return false; }
  constexpr const char* Label() const {
    return hot_ ? (FlushOrigin() ? "H+F" : "H") : (FlushOrigin() ? "F" : "U");
  }
  // 仅适配已冻结G0/G1/G2的历史位值；位2表达来源，不能当短寿命资格。
  // 新接口不改变历史策略，历史策略仍可能依据来源尝试混放。
  constexpr uint8_t LegacySourceMask() const {
    return (FlushOrigin() ? 2 : 0) | (hot_ ? 1 : 0);
  }

 private:
  Origin origin_;
  bool hot_;
};
}  // 文件事实接口不授予混放资格，也不返回Credit/Score。
