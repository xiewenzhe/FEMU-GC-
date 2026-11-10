#include "migration_order.h"
#include <cassert>
#include <set>
#include <iostream>
struct Extent { uint64_t zone_start, bytes; };
int main() {
  Extent a{1, 60}, b{2, 60}, c{1, 40}, d{2, 40};
  const std::map<std::string, std::vector<Extent*>> files{
      {"a", {&a}}, {"b", {&b}}, {"c", {&c}}, {"d", {&d}}};
  const auto simulate = [](const auto& ordered) {
    uint64_t remaining = 100, resets = 0;
    std::map<uint64_t, uint64_t> live{{1, 100}, {2, 100}};
    for (const auto& file : ordered) for (auto* e : file.second) {
      if (remaining < e->bytes) return resets;
      remaining -= e->bytes; live[e->zone_start] -= e->bytes;
      if (!live[e->zone_start]) { ++resets; remaining += 100; }
    }
    return resets;
  };
  // Filename order fills the only 100-byte target before either source clears.
  assert(simulate(files) == 0);
  std::map<std::string, std::vector<Extent*>> serial;
  std::set<Extent*> seen;
  unsigned ordinal = 0;
  for (const auto& zone : rrzc_oaza::SourceBatches(files))
    for (const auto& file : zone.second) {
      serial[std::to_string(ordinal++)] = file.second;
      for (auto* e : file.second) assert(seen.insert(e).second);
    }
  assert(seen == std::set<Extent*>({&a, &b, &c, &d}));
  assert(simulate(serial) == 2);
  // A single file crossing both sources is still split by the real source.
  const std::map<std::string, std::vector<Extent*>> spanning{{"one", {&a, &b, &c, &d}}};
  const auto groups = rrzc_oaza::SourceBatches(spanning);
  assert(groups.at(1).at("one") == std::vector<Extent*>({&a, &c}));
  assert(groups.at(2).at("one") == std::vector<Extent*>({&b, &d}));
  std::cout << "single-target progress and extent identity preservation passed\n";
}
