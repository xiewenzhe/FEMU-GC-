// 消融模式只控制协同目标入口；不改变标签、池策略、可靠性或选区模型。
#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <stdexcept>

namespace rrzc_placement {
enum class Mode : uint8_t { kLegacy, kG0, kG1, kG2 };

inline const char* Name(Mode mode) {
  switch (mode) {
    case Mode::kG0: return "G0";
    case Mode::kG1: return "G1";
    case Mode::kG2: return "G2";
    default: return "旧入口";
  }
}

struct Settings {
  Mode mode=Mode::kLegacy;
  bool Explicit() const { return mode!=Mode::kLegacy; }
  const char* PoolK() const {
    if (!Explicit()) return std::getenv("ZENFS_RRZC_POOL_K");
    return mode==Mode::kG0 ? "0" : "1";
  }
};

inline Settings ParseEnvironment() {
  const char* value=std::getenv("ZENFS_RRZC_PLACEMENT_MODE");
  if (!value) return {};
  Mode mode;
  if (!std::strcmp(value,"G0")) mode=Mode::kG0;
  else if (!std::strcmp(value,"G1")) mode=Mode::kG1;
  else if (!std::strcmp(value,"G2")) mode=Mode::kG2;
  else throw std::invalid_argument("放置模式必须明确为G0、G1或G2");
  // 新入口不接受第二组放置开关，即使数值看似相符也拒绝，防止隐式组合。
  for (const auto* key : {"ZENFS_RRZC_POOL_K", "ZENFS_RRZC_POOL_TAGS",
       "ZENFS_RRZC_STREAM_CLASSIFY", "ZENFS_RRZC_POOL_FLUSH_W",
       "ZENFS_RRZC_POOL_PAIR_ADMISSION", "ZENFS_RRZC_POOL_PAIR_OBSERVE",
       "ZENFS_RRZC_POOL_PAIR_COPY_BUDGET"}) {
    if (std::getenv(key))
      throw std::invalid_argument("显式放置模式与旧放置开关不能同时设置");
  }
  return {mode};
}

inline const Settings& ProcessSettings() {
  // 每个进程只解析一次；运行中修改环境不能切换已建立设备的模式。
  static const Settings settings=ParseEnvironment();
  return settings;
}

inline bool SkipHotMigration(Mode mode, bool new_file, uint8_t tag) {
  return mode==Mode::kG1 && !new_file && (tag&1);
}
}  // 消融模式命名空间结束。
