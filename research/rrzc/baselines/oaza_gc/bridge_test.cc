#include "rrzc_caza_bridge.h"
#include <cassert>
#include <iostream>
int main() {
  rrzc_caza::Registry registry;
  rrzc_related::Options options; options.levels = 4; options.base_level = 1;
  options.level_bytes = {1, 10, 100, 1000}; options.file_bytes = {10, 10, 10, 10};
  options.output_ratio = {1, 1, 1, 1};
  rrzc_caza::File file; file.number = 7; file.level = 3; file.smallest = "0010"; file.largest = "0020";
  file.bytes = 100;  // No guessed key count supplied by a cold MANIFEST metadata object.
  registry.Bind(1, "/db/000007.sst", {{100, 0}});
  registry.Publish("/db", {{"/db/000007.sst", "default", file}}, options, 10);
  bool failed = false;
  try { registry.Capture(1, "/db/000008.sst"); } catch (const std::invalid_argument&) { failed = true; }
  assert(failed);
  registry.RestoreKeys("/db/000007.sst", 11);
  auto snapshot = registry.Capture(1, "/db/000008.sst");
  assert(snapshot.files[0].keys == 11 && snapshot.files[0].level == 3);
  registry.Associate(8, 3); assert(registry.Association(8, 2) == 3);
  assert(registry.Association(9, 2) == 2);
  failed = false;
  try { registry.RestoreKeys("/db/000007.sst", 0); } catch (const std::invalid_argument&) { failed = true; }
  assert(failed && snapshot.files[0].keys == 11);
  std::cout << "OAZA unknown-count rejection/restoration/association metadata passed\n";
}
