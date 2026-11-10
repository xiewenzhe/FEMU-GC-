// Execution order only: preserve every selected extent and its file identity.
#pragma once
#include <cstdint>
#include <map>
#include <string>
#include <vector>
namespace rrzc_oaza {
template <class Extent>
std::map<uint64_t, std::map<std::string, std::vector<Extent*>>> SourceBatches(
    const std::map<std::string, std::vector<Extent*>>& files) {
  std::map<uint64_t, std::map<std::string, std::vector<Extent*>>> result;
  for (const auto& file : files)
    for (auto* extent : file.second)
      result[extent->zone_start][file.first].push_back(extent);
  return result;
}
}  // namespace rrzc_oaza
