#include "author_reference_backend.h"
#include "author_functions.inc"
#include <cassert>
#include <cmath>
static std::shared_ptr<ZoneFile> File(uint64_t size, int level, const char* low, const char* high) {
  auto file = std::make_shared<ZoneFile>(); file->size = size; file->level = level;
  file->low = low; file->high = high; return file;
}
int main(int argc, char**) {
  if (argc > 1) {
    // An inconsistent snapshot exercises the original no-progress path, bounded by the parent timeout.
    Backend backend; backend.files[1] = File(10, 0, "0000", "0001"); backend.ordered[0] = {999};
    backend.steps = 1; ZenFS fs(backend); fs.ReCalculateLifetimes(); return 2;
  }
  {
    Backend backend; Zone zone{100}; Extent small{&zone, 2}, large{&zone, 18};
    auto hot = File(2, 2, "0000", "0001"), cold = File(18, 2, "0002", "0003");
    hot->compacting = true; cold->trivial = true; hot->extents = {&small}; cold->extents = {&large};
    backend.files = {{1, hot}, {2, cold}}; backend.ordered[2] = {1, 2};
    ZenFS fs(backend); fs.ReCalculateLifetimes();
    const auto first = fs.zone_lifetime_map_.at(100);
    assert(first.file_count == 2 && first.total_lifetime / first.file_count == 0.5);
    // Same bytes, another extent boundary: original aggregation changes to 1/3, not byte-weighted 0.9.
    Extent half_a{&zone, 1}, half_b{&zone, 1}; hot->extents = {&half_a, &half_b};
    fs.ReCalculateLifetimes(); const auto split = fs.zone_lifetime_map_.at(100);
    assert(split.file_count == 3 && std::abs(split.total_lifetime / split.file_count - 1.0 / 3) < 1e-12);
  }
  {
    Backend backend; backend.files[1] = File(10, 0, "0000", "0009");
    backend.files[2] = File(10, 0, "0000", "0009"); backend.files[3] = File(10, 1, "0000", "0009");
    backend.files[3]->trivial = true; backend.ordered[0] = {1, 2}; backend.ordered[1] = {3};
    backend.vertical[0] = 0.5; backend.vertical[1] = 0.01; backend.steps = 1;
    ZenFS fs(backend); fs.ReCalculateLifetimes();
    assert(fs.fno_already_propagated == std::set<uint64_t>({1, 2, 3}));
    assert(fs.level_file_map_.at(1).front().sst_lifetime_value_ == fs.level_file_map_.at(0).front().sst_lifetime_value_);
    assert(backend.propagated == 1);
  }
  std::cout << "author algorithms: extent aggregation and one-step propagation verified; field backend only\n";
}
