// Field-only backend for executing the author's unmodified algorithm bodies.
// No filesystem, compaction execution, placement decisions or device I/O here.
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <unordered_set>
#include <vector>
#include "rocksdb/slice.h"
using rocksdb::Slice;
struct Zone { uint64_t start_; };
struct Extent { Zone* zone_; uint64_t length_; };
struct ZoneFile {
  uint64_t size = 0; int level = 0; std::string low, high;
  bool compacting = false, trivial = false, deleted = false, is_wal_ = false;
  std::vector<Extent*> extents;
  bool IsDeleted() const { return deleted; }
  uint64_t GetFileSize() const { return size; }
  const std::vector<Extent*>& GetExtents() const { return extents; }
};
struct Backend {
  std::map<uint64_t, std::shared_ptr<ZoneFile>> files;
  std::map<int, std::vector<uint64_t>> ordered;
  std::array<double, 6> vertical{};
  uint64_t steps = 0, propagated = 0;
  void SameLevelFileList(int level, std::vector<uint64_t>& output, std::set<uint64_t>& compacting) {
    output = ordered[level];
    for (auto number : output) if (files.count(number) && files.at(number)->compacting) compacting.insert(number);
  }
  void TrivialMoveFiles(int level, std::set<uint64_t>& output) {
    for (const auto& item : files) if (item.second->level == level && item.second->trivial) output.insert(item.first);
  }
  double PredictCompactionScore(int level) const { return vertical.at(level); }
  uint64_t GetPredictCnt() const { return steps; }
  double GetAlphaValue() const { return 0.5; }
  ZoneFile* GetSSTZoneFileInZBDNoLock(uint64_t number) {
    return files.count(number) ? files.at(number).get() : nullptr;
  }
  std::array<uint64_t, 10> GetCurrentLSMTree() const {
    std::array<uint64_t, 10> result{};
    for (const auto& item : files) if (!item.second->deleted) result.at(item.second->level) += item.second->size;
    return result;
  }
  double PredictCompactionScoreTmp(int level, std::array<uint64_t, 10>& tree, int l0_count) const {
    uint64_t limit = 1000; for (int l = 1; l < level; ++l) limit *= 10;
    const double size_score = static_cast<double>(tree.at(level)) / limit;
    return level == 0 ? std::max(size_score, static_cast<double>(l0_count) / 4) : size_score;
  }
  bool GetMinMaxKey(uint64_t number, Slice& low, Slice& high) {
    if (!files.count(number)) return false;
    low = files.at(number)->low; high = files.at(number)->high; return true;
  }
  void DownwardAdjacentFileList(Slice& low, Slice& high, int level, std::vector<uint64_t>& output) {
    for (const auto& item : files) {
      const auto& file = *item.second;
      if (file.level == level + 1 && !file.compacting &&
          !(Slice(file.high).compare(low) < 0 || high.compare(Slice(file.low)) < 0)) output.push_back(item.first);
    }
  }
  double GetAvgCompressibilityOflevel(int) const { return 1.0; }
  void AddPropagationCount(uint64_t count) { propagated += count; }
};
class ZenFS {
 public:
  struct FileInfo_ {
    uint64_t fno; double horizontal_lifetime; bool is_compacting, is_trivial; double sst_lifetime_value_;
  };
  struct ZoneLifetimeData {
    double total_lifetime = 0; int file_count = 0; std::map<uint64_t, double> file_lifetimes;
  };
  Backend* zbd_; void* db_ptr_ = this;
  std::map<std::string, std::shared_ptr<ZoneFile>> files_;
  std::map<int, std::vector<FileInfo_>> level_file_map_;
  std::map<uint64_t, ZoneLifetimeData> zone_lifetime_map_;
  std::set<uint64_t> fno_already_propagated, fno_not_should_selected_as_pivot_again;
  explicit ZenFS(Backend& backend) : zbd_(&backend) {}
  void CalculateHorizontalLifetimes(std::map<int, std::vector<FileInfo_>>&);
  void ReCalculateLifetimes();
  void PredictCompaction(int);
  void GetOverlappingFno(uint64_t, uint64_t, std::vector<uint64_t>&);
  void Propagation(uint64_t, std::vector<uint64_t>);
  uint64_t GetMaxLevelScoreLevel(std::array<uint64_t, 10>&, int, std::unordered_set<int>&);
  uint64_t GetMaxHorizontalFno(int);
};
