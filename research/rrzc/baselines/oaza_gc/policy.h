// TACO 22(2), article 57: Algorithms 1/2/3 and Equations 1..4.
// The caller must explicitly choose an interpretation of the ambiguous Algorithm 3.
#pragma once
#include "rrzc_related_snapshot.h"
#include <tuple>
namespace rrzc_oaza {
using rrzc_related::File;
inline long double Ratio(const File& file, int reference, const std::vector<File>& files) {
  if (!file.keys) throw std::invalid_argument("OAZA requires non-dummy key counts");
  rrzc_related::Wide overlap_keys = 0;
  for (const auto& f : files)
    if (!f.wal && f.number != file.number && f.level == reference && rrzc_related::Overlap(file, f))
      overlap_keys += f.keys;  // Full overlapping SST ranges, not intersection length or extent bytes.
  return static_cast<long double>(overlap_keys) / file.keys;
}
enum class Placement { kTopLifetime, kFreshAndPromote, kBottomLifetime, kRatio };
struct Plan {
  Placement stage;
  int associated_level;
  long double ratio;
  std::vector<uint64_t> nearest_files;
};
inline Plan Allocate(const File& file, const std::vector<File>& files, int max_level) {
  if (max_level < 1 || file.level < 0 || file.level > max_level)
    throw std::invalid_argument("invalid OAZA level");
  if (file.level <= 1) return {Placement::kTopLifetime, file.level, 0, {}};
  const int reference = file.level == max_level ? file.level - 1 : file.level + 1;
  const auto ratio = Ratio(file, reference, files);
  if (ratio == 0) return {file.level < reference ? Placement::kFreshAndPromote : Placement::kBottomLifetime,
                         file.level < reference ? reference : file.level, ratio, {}};
  std::vector<std::pair<long double, uint64_t>> neighbors;
  for (const auto& f : files) if (!f.wal && f.number != file.number && f.level == file.level && f.keys)
    neighbors.emplace_back(std::fabs(Ratio(f, reference, files) - ratio), f.number);
  std::sort(neighbors.begin(), neighbors.end());
  Plan result{Placement::kRatio, file.level, ratio, {}};
  for (const auto& n : neighbors) result.nearest_files.push_back(n.second);
  return result;  // Native allocator must check whole-file fit and lifetime fallback.
}
inline uint64_t Quantity(uint64_t invalid, uint64_t allocated_capacity, uint64_t total_zones,
                         long double threshold = 0.15L, long double average_invalid = 0.5L) {
  if (!allocated_capacity || invalid > allocated_capacity || !(threshold > 0 && threshold < 1) ||
      !(average_invalid > 0 && average_invalid <= 1))
    throw std::invalid_argument("invalid OAZA quantity inputs");
  if (threshold == 0.15L && average_invalid == 0.5L) {
    // Exactly floor(3 * invalid * zones / (10 * capacity)), including integer
    // boundaries; quotient/remainder avoid overflowing the 128-bit product.
    const auto product = static_cast<rrzc_related::Wide>(invalid) * total_zones;
    const auto denominator = static_cast<rrzc_related::Wide>(allocated_capacity) * 10;
    return static_cast<uint64_t>((product / denominator) * 3 +
        ((product % denominator) * 3) / denominator);
  }
  // Algebraically cancel PROP_RESET and (1-GC_TS) in Equations 2..4.
  const auto n = threshold * (static_cast<long double>(invalid) / allocated_capacity) /
                 average_invalid * total_zones;
  if (n >= total_zones) return total_zones;
  return static_cast<uint64_t>(n);  // Explicit integer floor; paper does not specify rounding.
}
enum class CostInterpretation { kPrintedLastFileValidU, kProseLastFileInvalidU,
                                kAdaptedByteWeightedValidCost };
struct Part { uint64_t file, bytes; long double ratio; };
struct Zone { uint64_t start, valid, capacity, label; std::vector<Part> parts; };
inline long double Priority(long double ratio, long double amplification) {
  if (!(amplification > 0) || ratio < 0 || !std::isfinite(ratio))
    throw std::invalid_argument("invalid OAZA migration inputs");
  return ratio == 0 || ratio > 2 * amplification ? 1 : ratio / amplification;
}
inline long double Cost(const Zone& z, long double amplification, CostInterpretation interpretation) {
  if (!z.capacity || z.valid > z.capacity || z.parts.empty())
    throw std::invalid_argument("invalid OAZA Zone composition");
  uint64_t covered = 0;
  long double pri = 0;
  for (const auto& p : z.parts) {
    covered = rrzc_related::Add(covered, p.bytes);
    const auto value = Priority(p.ratio, amplification);
    if (interpretation == CostInterpretation::kAdaptedByteWeightedValidCost) pri += value * p.bytes;
    else pri = value;  // Literal Algorithm 3 overwrites at every file; order is caller-visible.
  }
  if (covered != z.valid || !covered) throw std::invalid_argument("incomplete OAZA Zone composition");
  const auto valid = static_cast<long double>(z.valid) / z.capacity;
  if (interpretation == CostInterpretation::kAdaptedByteWeightedValidCost)
    return valid * (pri / covered) * z.label;  // Explicit adaptation, never mislabeled literal paper.
  return (interpretation == CostInterpretation::kPrintedLastFileValidU ? 1 - valid : valid) * pri * z.label;
}
inline std::vector<uint64_t> Select(const std::vector<Zone>& zones, uint64_t count,
                                  long double amplification, CostInterpretation interpretation) {
  std::vector<std::pair<long double, uint64_t>> sorted;
  std::set<uint64_t> seen;
  for (const auto& z : zones) {
    if (!seen.insert(z.start).second) throw std::invalid_argument("duplicate OAZA Zone");
    sorted.emplace_back(Cost(z, amplification, interpretation), z.start);
  }
  std::sort(sorted.begin(), sorted.end());
  std::vector<uint64_t> result;
  for (size_t i = 0; i < std::min<uint64_t>(count, sorted.size()); ++i) result.push_back(sorted[i].second);
  return result;
}
}  // namespace rrzc_oaza
