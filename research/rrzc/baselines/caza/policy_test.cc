#include "policy.h"
#include <cassert>
#include <iostream>

using namespace rrzc_caza;

static std::string Key(unsigned value) {
  std::string result(16, '\0');
  result[14] = static_cast<char>((value >> 8) & 255);
  result[15] = static_cast<char>(value & 255);
  return result;
}

static File Sst(uint64_t id, int level, unsigned low, unsigned high,
                std::vector<ZoneId> zones) {
  return {id, level, Key(low), Key(high), std::move(zones)};
}

struct Backend {
  std::map<ZoneId, Attempt> existing;
  Attempt empty = Attempt::kUnavailable, liza = Attempt::kUnavailable;
  std::vector<std::string> calls;
  Attempt Existing(ZoneId zone) {
    calls.push_back(std::to_string(zone.start));
    return existing.count(zone) ? existing.at(zone) : Attempt::kUnavailable;
  }
  Attempt Empty() { calls.push_back("empty"); return empty; }
  Attempt Liza() { calls.push_back("liza"); return liza; }
};

int main() {
  const ZoneId a{100, 4}, b{200, 1}, c{300, 2}, d{400, 3};
  const Request request{99, 1, Key(20), Key(40)};
  std::vector<File> files = {
      Sst(1, 2, 10, 30, {a, a}), Sst(2, 2, 25, 40, {a, b}),
      Sst(3, 2, 30, 60, {b}), Sst(4, 2, 35, 35, {a}),
      Sst(5, 2, 70, 90, {d}), Sst(6, 0, 20, 40, {d}),
      Sst(7, 1, 0, 18, {c}), Sst(8, 1, 43, 50, {d})};
  auto ranking = RankOverlap(request, files);
  assert(ranking.size() == 2 && ranking[0].zone == a && ranking[0].overlapping_files == 3);
  assert(ranking[1].zone == b && ranking[1].overlapping_files == 2);
  // Same file, repeated extent: count once; an upper-level overlap is excluded.
  files[0].zones.push_back(a);
  assert(RankOverlap(request, files)[0].overlapping_files == 3);
  auto near = RankNearest(request, files);
  assert(near.size() == 2 && near[0] == c && near[1] == d);
  assert(Difference(Key(256), Key(255)) == Key(1));

  Backend stale;
  stale.existing[b] = Attempt::kAcquired;  // Highest-ranked generation/target unavailable.
  assert(Allocate(request, files, stale) == Stage::kOverlap);
  assert((stale.calls == std::vector<std::string>{"100", "200"}));

  Backend fresh;
  fresh.empty = Attempt::kAcquired;
  fresh.existing[c] = Attempt::kAcquired;
  assert(Allocate(request, files, fresh) == Stage::kEmpty);
  assert((fresh.calls == std::vector<std::string>{"100", "200", "empty"}));

  Backend closest;
  closest.existing[c] = Attempt::kAcquired;
  assert(Allocate(request, files, closest) == Stage::kNearest);
  assert((closest.calls == std::vector<std::string>{"100", "200", "empty", "300"}));

  Backend fallback;
  fallback.liza = Attempt::kAcquired;
  assert(Allocate(request, files, fallback) == Stage::kLiza);
  assert((fallback.calls == std::vector<std::string>{"100", "200", "empty", "300", "400", "liza"}));

  Backend io_error;
  io_error.existing[a] = Attempt::kError;
  assert(Allocate(request, files, io_error) == Stage::kError);
  assert((io_error.calls == std::vector<std::string>{"100"}));
  Backend empty_error;
  empty_error.empty = Attempt::kError;
  assert(Allocate(request, files, empty_error) == Stage::kError);
  assert(empty_error.calls.back() == "empty");

  bool rejected = false;
  auto duplicate = files;
  duplicate.push_back(files.front());
  try { RankOverlap(request, duplicate); } catch (const std::invalid_argument&) { rejected = true; }
  assert(rejected);
  rejected = false;
  try { RankNearest(Request{100, 1, "a", "zz"}, {}); }
  catch (const std::invalid_argument&) { rejected = true; }
  assert(rejected);

  // Empty LSM snapshot still attempts an empty zone, then LIZA; no invented overlap.
  Backend initial;
  initial.liza = Attempt::kAcquired;
  assert(Allocate(request, {}, initial) == Stage::kLiza);
  assert((initial.calls == std::vector<std::string>{"empty", "liza"}));
  // Inclusive boundary overlap and deterministic equal-count ordering.
  auto boundary = RankOverlap(request, {Sst(20, 2, 40, 50, {b}), Sst(21, 2, 10, 20, {a})});
  assert(boundary.size() == 2 && boundary[0].zone == a && boundary[1].zone == b);
  std::cout << "CAZA policy checks passed; backend is a stub, not device acceptance\n";
}
