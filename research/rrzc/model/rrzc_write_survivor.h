// 当前挂载会话内的成功迁移证据；不是文件寿命预测，不写入磁盘格式。
#pragma once
#include "rrzc_placement_mode.h"
#include <cstdint>

namespace rrzc_write_survivor {
enum class State : uint8_t { kNew = 0, kRecoveredUnknown = 1, kCommitted = 2 };

class History {
 public:
  State Get() const { return state_; }
  void Recovered() { state_ = State::kRecoveredUnknown; }
  // 调用方持有文件WR锁；成功提交过任一实际迁移片段即具备证据。
  // 部分片段已提交也算，不能把一次部分迁移描述成整个文件全部搬过。
  void AfterMapping(bool flush_origin, bool committed, bool still_live) {
    if (flush_origin && committed && still_live) state_ = State::kCommitted;
  }
 private:
  State state_ = State::kNew;
};

inline bool Enabled(rrzc_placement::Mode mode) {
  return mode == rrzc_placement::Mode::kG2WriteSurvivor;
}

inline bool Skip(rrzc_placement::Mode mode, bool append, uint8_t tag, State history) {
  return Enabled(mode) && !append && tag == 2 && history == State::kCommitted;
}
}  // 当前会话迁移历史结束。
