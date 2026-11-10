#include "rrzc_caza_bridge.h"
#include <cassert>
#include <thread>
#include <iostream>
using namespace rrzc_caza;
int main() {
  Registry registry;
  const auto file = registry.Register("/db/000007.sst");
  Request request;
  assert(!registry.Read(file, &request));
  registry.Publish("/db", {});
  assert(registry.Capture(10, "/db/000007.sst").files.empty());
  registry.Ready("/db/000007.sst", 0, "0010", "0030");
  assert(registry.Read(file, &request) && request.level == 0 && request.number == 7);
  registry.Bind(10, "/db/000001.sst", {{100, 0}, {100, 0}});
  registry.Publish("/db", {{"/db/000001.sst", "default", {1, 1, "0010", "0020", {}}}});
  auto snap = registry.Capture(10, "/db/000007.sst");
  assert(snap.files.size() == 1 && RankOverlap(request, snap.files)[0].overlapping_files == 1);
  registry.Bind(10, "/db/000001.sst", {{200, 1}});
  auto moved = registry.Capture(10, "/db/000007.sst");
  assert(moved.version > snap.version && moved.files[0].zones[0].start == 200);
  // A captured snapshot is immutable, and a different device cannot reuse bindings.
  assert(snap.files[0].zones[0].start == 100);
  bool failed = false;
  try { registry.Capture(20, "/db/000007.sst"); }
  catch (const std::invalid_argument&) { failed = true; }
  assert(failed);
  registry.Publish("/db", {});  // File invalidation and trivial moves use committed versions.
  assert(registry.Capture(10, "/db/000007.sst").files.empty());
  Context context{request, snap};
  assert(current == nullptr);
  { Scope outer(context); assert(current == &context);
    std::thread thread([] { assert(current == nullptr); }); thread.join();
    { Scope nested(context); assert(current == &context); }
    assert(current == &context);
  }
  assert(current == nullptr);
  registry.Unregister("/db/000007.sst", file);
  auto replacement = registry.Register("/db/000007.sst");
  registry.Unregister("/db/000007.sst", file);  // Old handle cannot unregister new one.
  registry.Ready("/db/000007.sst", 1, "0010", "0020");
  assert(registry.Read(replacement, &request) && request.level == 1);
  std::cout << "CAZA metadata publication/mapping/generation/thread scope tests passed\n";
}
