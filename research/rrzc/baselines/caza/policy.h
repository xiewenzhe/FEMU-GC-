// CAZA, HotStorage 2022, section 4: count overlapping SSTs, then empty,
// same-level nearest key range, then LIZA. No RR scores or future lifetimes.
// This module does not acquire Zone tokens or perform any filesystem I/O.
#pragma once
#include <algorithm>
#include <cstdint>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace rrzc_caza {

struct ZoneId {
  uint64_t start = 0;
  uint64_t generation = 0;
  bool operator<(const ZoneId& other) const {
    return std::tie(start, generation) < std::tie(other.start, other.generation);
  }
  bool operator==(const ZoneId& other) const {
    return start == other.start && generation == other.generation;
  }
};

struct File {
  uint64_t number = 0;
  int level = -1;
  std::string smallest, largest;  // user keys, never InternalKey sequence suffixes
  std::vector<ZoneId> zones;
};

struct Request {
  uint64_t number = 0;
  int level = -1;
  std::string smallest, largest;
};

struct RankedZone {
  ZoneId zone;
  uint64_t overlapping_files = 0;
};

inline void Validate(const Request& request, const std::vector<File>& files) {
  if (request.level < 0 || request.smallest.empty() || request.largest < request.smallest)
    throw std::invalid_argument("CAZA requires a known ordered user-key range and level");
  std::set<uint64_t> seen;
  for (const auto& file : files) {
    if (file.level < 0 || file.smallest.empty() || file.largest < file.smallest ||
        !seen.insert(file.number).second)
      throw std::invalid_argument("CAZA snapshot has unknown/duplicate file metadata");
  }
}

inline bool Overlaps(const Request& request, const File& file) {
  return !(file.largest < request.smallest || request.largest < file.smallest);
}

inline std::vector<RankedZone> RankOverlap(const Request& request,
                                         const std::vector<File>& files) {
  Validate(request, files);
  std::map<ZoneId, uint64_t> count;
  for (const auto& file : files) {
    if (file.number == request.number || file.level != request.level + 1 || !Overlaps(request, file))
      continue;
    // Multiple extents of the same SST in one Zone count once, regardless of bytes.
    const std::set<ZoneId> unique(file.zones.begin(), file.zones.end());
    for (const auto& zone : unique) ++count[zone];
  }
  std::vector<RankedZone> result;
  for (const auto& item : count) result.push_back({item.first, item.second});
  std::sort(result.begin(), result.end(), [](const auto& a, const auto& b) {
    if (a.overlapping_files != b.overlapping_files)
      return a.overlapping_files > b.overlapping_files;
    return a.zone < b.zone;  // Paper leaves equal-count order unspecified.
  });
  return result;
}

// Explicit adaptation for fixed-width, big-endian bytewise benchmark keys.
// The paper does not define a general distance for arbitrary comparators.
inline std::string Difference(const std::string& high, const std::string& low) {
  if (high.size() != low.size() || high < low)
    throw std::invalid_argument("nearest-key distance requires equal-width ordered keys");
  std::string result(high.size(), '\0');
  int borrow = 0;
  for (size_t i = high.size(); i-- > 0;) {
    int value = static_cast<unsigned char>(high[i]) -
                static_cast<unsigned char>(low[i]) - borrow;
    borrow = value < 0 ? 1 : 0;
    if (borrow) value += 256;
    result[i] = static_cast<char>(value);
  }
  return result;
}

inline std::vector<ZoneId> RankNearest(const Request& request, const std::vector<File>& files) {
  Validate(request, files);
  struct Neighbor { std::string distance; uint64_t number; ZoneId zone; };
  std::vector<Neighbor> neighbors;
  if (request.smallest.size() != request.largest.size())
    throw std::invalid_argument("nearest-key distance requires fixed-width keys");
  for (const auto& file : files) {
    if (file.number == request.number || file.level != request.level) continue;
    if (file.smallest.size() != request.smallest.size() ||
        file.largest.size() != request.smallest.size())
      throw std::invalid_argument("nearest-key distance requires fixed-width keys");
    std::string distance(request.smallest.size(), '\0');
    if (file.largest < request.smallest) distance = Difference(request.smallest, file.largest);
    else if (request.largest < file.smallest) distance = Difference(file.smallest, request.largest);
    const std::set<ZoneId> unique(file.zones.begin(), file.zones.end());
    for (const auto& zone : unique) neighbors.push_back({distance, file.number, zone});
  }
  std::sort(neighbors.begin(), neighbors.end(), [](const auto& a, const auto& b) {
    if (a.distance != b.distance) return a.distance < b.distance;
    if (a.number != b.number) return a.number < b.number;
    return a.zone < b.zone;
  });
  std::set<ZoneId> seen;
  std::vector<ZoneId> result;
  for (const auto& item : neighbors)
    if (seen.insert(item.zone).second) result.push_back(item.zone);
  return result;
}

enum class Attempt { kAcquired, kUnavailable, kError };
enum class Stage { kOverlap, kEmpty, kNearest, kLiza, kError };

// Backend contract: existing-zone acquisition must recheck generation,
// capacity, exclusivity, contents, and tokens. Empty() may reset only unreferenced
// zones. Error is terminal; unavailability allows the next documented fallback.
template<class Backend>
Stage Allocate(const Request& request, const std::vector<File>& files, Backend& backend) {
  for (const auto& item : RankOverlap(request, files)) {
    const auto attempt = backend.Existing(item.zone);
    if (attempt == Attempt::kAcquired) return Stage::kOverlap;
    if (attempt == Attempt::kError) return Stage::kError;
  }
  const auto empty = backend.Empty();
  if (empty == Attempt::kAcquired) return Stage::kEmpty;
  if (empty == Attempt::kError) return Stage::kError;
  for (const auto& zone : RankNearest(request, files)) {
    const auto attempt = backend.Existing(zone);
    if (attempt == Attempt::kAcquired) return Stage::kNearest;
    if (attempt == Attempt::kError) return Stage::kError;
  }
  return backend.Liza() == Attempt::kAcquired ? Stage::kLiza : Stage::kError;
}

}  // namespace rrzc_caza
