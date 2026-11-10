#include "rrzc_caza_bridge.h"
#include <cassert>
#include <iostream>
int main() {
  using namespace rrzc_caza;
  Registry registry;
  rrzc_related::Options options;
  options.levels = 3; options.level_bytes = {1, 100, 1000};
  options.file_bytes = {64, 64, 64}; options.output_ratio = {1, 1, 1};
  File file; file.number = 1; file.level = 1; file.smallest = "0001"; file.largest = "0010";
  file.bytes = 42; file.epoch = 7;
  registry.Bind(10, "/db/000001.sst", {{100, 0}});
  registry.Publish("/db", {{"/db/000001.sst", "default", file}}, options);
  auto initial = registry.Capture(10, "/db/000002.sst");
  assert(initial.files[0].bytes == 42 && initial.files[0].epoch == 7 && initial.compactions == 0);
  registry.Mark(1, true); assert(registry.Capture(10, "/db/000002.sst").files[0].compacting);
  registry.Mark(1, false); assert(!registry.Capture(10, "/db/000002.sst").files[0].compacting);
  assert(!initial.files[0].compacting);  // Frozen frames never borrow mutable RDB pointers.
  registry.Completed(2, 100, 73); registry.Completed(2, 200, 100);
  auto after = registry.Capture(10, "/db/000002.sst");
  assert(after.compactions == 2 && after.version > initial.version);
  assert(std::fabs(after.cold_options.output_ratio[2] - 0.615L) < 1e-12L);
  registry.Completed(2, 0, 0);  // Trivial move counted, not used as a bogus compression observation.
  assert(registry.Capture(10, "/db/000002.sst").compactions == 3);
  registry.Publish("/db2", {}, options);
  bool rejected = false;
  try { registry.Capture(10, "/db/000002.sst"); } catch (const std::invalid_argument&) { rejected = true; }
  assert(rejected);
  std::cout << "ColdMap native bridge size/compacting/version/completion/compression isolation passed\n";
}
