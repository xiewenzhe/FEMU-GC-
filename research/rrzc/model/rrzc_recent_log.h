// 独立C2h协议解析，不修改旧C1h数据结构或RR调用路径。
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace rrzc_v1 {
constexpr uint64_t kSecond = 1000000000;
constexpr uint32_t kEnabled = 1, kValid10 = 2, kValid30 = 4, kLoss = 8;
constexpr size_t kHeaderBytes = 56, kEntryBytes = 88;
struct RecentBlock {
  uint64_t zone = 0, generation = 0, snapshot_ns = 0, epoch_start_ns = 0;
  uint64_t rc = 0, delta10 = 0, delta30 = 0;
  uint32_t zone_index = 0, block_id = 0, ch = 0, lun = 0, plane = 0, block = 0, flags = 0;
};
struct RecentReport {
  uint64_t threshold = 0, session_hi = 0, session_lo = 0;
  uint32_t blocks_per_zone = 0;
  std::vector<RecentBlock> zones;
};
inline uint64_t Little(const uint8_t* p, size_t n) {
  uint64_t value = 0;
  for (size_t i = 0; i < n; ++i) value |= uint64_t(p[i]) << (8*i);
  return value;
}
inline bool DecodeRecent(const uint8_t* data, size_t size, uint32_t zones,
                         uint64_t zone_size, RecentReport* output, std::string* error) {
  *output = {};
  auto fail = [&](const char* why) { *error = why; return false; };
  if (!zones || !zone_size || size < kHeaderBytes || !data ||
      zones > (SIZE_MAX-kHeaderBytes)/kEntryBytes ||
      size != kHeaderBytes+size_t(zones)*kEntryBytes)
    return fail("C2h缓冲区长度或几何不合法");
  if (Little(data,4) != 1 || Little(data+4,4) != kEntryBytes ||
      Little(data+8,4) != zones || !Little(data+12,4) ||
      Little(data+40,8) != 10*kSecond || Little(data+48,8) != 30*kSecond)
    return fail("C2h版本、条目或窗口不匹配");
  RecentReport result;
  result.blocks_per_zone = Little(data+12,4);
  result.threshold = Little(data+16,8);
  result.session_hi = Little(data+24,8); result.session_lo = Little(data+32,8);
  if (!result.threshold || !(result.session_hi || result.session_lo))
    return fail("C2h没有有效阈值或设备会话");
  if (uint64_t(zones-1) > UINT64_MAX/zone_size)
    return fail("C2h区地址溢出");
  for (uint32_t i = 0; i < zones; ++i) {
    const uint8_t* p = data+kHeaderBytes+size_t(i)*kEntryBytes;
    RecentBlock b;
    b.zone = uint64_t(i)*zone_size;
    b.generation=Little(p+8,8); b.snapshot_ns=Little(p+16,8);
    b.epoch_start_ns=Little(p+24,8); b.rc=Little(p+32,8);
    b.delta10=Little(p+40,8); b.delta30=Little(p+48,8);
    b.zone_index=Little(p+56,4); b.block_id=Little(p+60,4);
    b.ch=Little(p+64,4); b.lun=Little(p+68,4); b.plane=Little(p+72,4);
    b.block=Little(p+76,4); b.flags=Little(p+80,4);
    if ((i == 0 && Little(p,8) != 0) || b.zone_index != i ||
        b.block_id >= result.blocks_per_zone || (b.flags & ~uint32_t(15)) ||
        !(b.flags & kEnabled) || Little(p+84,4) != 0 ||
        b.epoch_start_ns > b.snapshot_ns || b.delta10 > b.delta30 || b.delta30 > b.rc)
      return fail("C2h条目身份、计数或状态不合法");
    const uint64_t age = b.snapshot_ns-b.epoch_start_ns;
    if (((b.flags & kValid10) && age < 10*kSecond) ||
        ((b.flags & kValid30) && (age < 30*kSecond || !(b.flags & kValid10))) ||
        ((b.flags & kLoss) && (b.flags & kValid30)))
      return fail("C2h窗口完整性标志矛盾");
    result.zones.push_back(b);
  }
  *output = std::move(result); error->clear(); return true;
}
}  // 命名空间结束。
