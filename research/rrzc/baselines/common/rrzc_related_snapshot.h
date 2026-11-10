// Value-only inputs shared by the isolated paper models. No filesystem callbacks.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace rrzc_related {
struct File {
  uint64_t number = 0;
  int level = 0;
  std::string low, high;
  uint64_t bytes = 0, keys = 0, epoch = 0;
  bool compacting = false, wal = false;
};
struct Options {
  int levels = 7, base_level = 1;
  uint64_t l0_trigger = 4;
  std::vector<uint64_t> level_bytes, file_bytes;
  std::vector<long double> output_ratio;
};
inline bool Overlap(const File& a, const File& b) {
  return !(a.high < b.low || b.high < a.low);
}
inline void Validate(const std::vector<File>& files, const Options& options) {
  if (options.levels < 2 || options.base_level < 1 || options.base_level >= options.levels ||
      !options.l0_trigger || options.level_bytes.size() != static_cast<size_t>(options.levels) ||
      options.file_bytes.size() != static_cast<size_t>(options.levels) ||
      options.output_ratio.size() != static_cast<size_t>(options.levels))
    throw std::invalid_argument("incomplete LSM options");
  std::set<uint64_t> seen;
  for (const auto& f : files) {
    if (!seen.insert(f.number).second || !f.bytes || (!f.wal &&
        (f.level < 0 || f.level >= options.levels || f.low.empty() || f.high < f.low)))
      throw std::invalid_argument("invalid/duplicate LSM file");
  }
  for (int l = 1; l < options.levels; ++l)
    if (!options.level_bytes[l] || !options.file_bytes[l] ||
        !(options.output_ratio[l] > 0) || !std::isfinite(options.output_ratio[l]))
      throw std::invalid_argument("invalid LSM level bounds/compression estimate");
}
using Wide = unsigned __int128;
inline Wide KeyInteger(const std::string& key) {
  if (key.empty() || key.size() > 16) throw std::invalid_argument("key must have 1..16 fixed bytes");
  Wide value = 0;
  for (unsigned char c : key) value = (value << 8) | c;
  return value;
}
inline std::string KeyString(Wide value, size_t width) {
  std::string result(width, '\0');
  for (size_t i = width; i-- > 0;) { result[i] = static_cast<char>(value & 255); value >>= 8; }
  if (value) throw std::overflow_error("predicted key exceeds fixed width");
  return result;
}
// Exact floor(span * offset / total), avoiding a 128-by-64-bit product overflow.
inline Wide Fraction(Wide span, uint64_t offset, uint64_t total) {
  if (!total || offset > total) throw std::invalid_argument("invalid key interpolation");
  return (span / total) * offset + ((span % total) * offset) / total;
}
inline uint64_t Add(uint64_t a, uint64_t b) {
  if (b > UINT64_MAX - a) throw std::overflow_error("metadata byte sum overflow");
  return a + b;
}
inline uint64_t PhysicalBound(uint64_t bytes, uint64_t buffer, uint64_t header, uint64_t block) {
  if (!buffer || !block) throw std::invalid_argument("invalid native replay buffer/block size");
  const auto aligned = [&](Wide value) { return ((value + block - 1) / block) * block; };
  const Wide full = aligned(static_cast<Wide>(buffer) + header);
  const auto tail = bytes % buffer;
  const Wide total = static_cast<Wide>(bytes / buffer) * full + (tail ? aligned(static_cast<Wide>(tail) + header) : 0);
  if (total > UINT64_MAX) throw std::overflow_error("physical replay size overflow");
  return static_cast<uint64_t>(total);
}
}  // namespace rrzc_related
