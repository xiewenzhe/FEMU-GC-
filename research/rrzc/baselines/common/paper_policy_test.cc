#include "../coldmap/policy.h"
#include "../oaza_gc/policy.h"
#include <cassert>
#include <iostream>
using rrzc_related::File;
using rrzc_related::Options;
static std::string Key(uint64_t value) { return rrzc_related::KeyString(value, 8); }
static File F(uint64_t id, int level, uint64_t low, uint64_t high, uint64_t bytes, uint64_t keys = 1) {
  File f; f.number = id; f.level = level; f.low = Key(low); f.high = Key(high);
  f.bytes = bytes; f.keys = keys; f.epoch = id; return f;
}
static Options Config(int levels = 3) {
  Options o; o.levels = levels; o.level_bytes.assign(levels, 100);
  o.file_bytes.assign(levels, 64); o.output_ratio.assign(levels, 1); return o;
}
static void Near(long double a, long double b) { assert(std::fabs(a - b) < 1e-12L); }
template<class Function> static void Invalid(Function fn) {
  bool caught = false; try { fn(); } catch (const std::invalid_argument&) { caught = true; }
  assert(caught);
}
int main() {
  assert(rrzc_related::PhysicalBound(1048568, 1048568, 8, 4096) == 1048576);
  assert(rrzc_related::PhysicalBound(1048569, 1048568, 8, 4096) == 1048576 + 4096);
  assert(rrzc_related::PhysicalBound(1048577, 1048576, 0, 4096) == 1048576 + 4096);
  assert(rrzc_related::PhysicalBound(0, 1048568, 8, 4096) == 0);
  using namespace rrzc_oaza;
  auto request = F(1, 2, 10, 20, 100, 10);
  std::vector<File> files{F(2, 3, 0, 12, 100, 30), F(3, 3, 16, 25, 100, 20),
                          F(4, 3, 15, 17, 100, 0), F(5, 3, 90, 100, 100, 1000)};
  Near(Ratio(request, 3, files), 5);  // 30+20 keys in overlapping files; not 3+5 intersecting span.
  auto plan = Allocate(request, files, 4); assert(plan.stage == Placement::kRatio);
  auto empty = Allocate(request, {}, 4); assert(empty.stage == Placement::kFreshAndPromote && empty.associated_level == 3);
  auto bottom = request; bottom.level = 4;
  assert(Allocate(bottom, {}, 4).stage == Placement::kBottomLifetime);
  auto top = request; top.level = 1;
  assert(Allocate(top, {}, 4).stage == Placement::kTopLifetime);
  request.keys = 0; Invalid([&] { Ratio(request, 3, files); });
  assert(Quantity(20, 100, 256) == 15 && Quantity(0, 100, 256) == 0);
  assert(Quantity(5, 96, 64) == 1);
  assert(Quantity(UINT64_MAX, UINT64_MAX, UINT64_MAX) ==
         static_cast<uint64_t>(static_cast<rrzc_related::Wide>(UINT64_MAX) * 3 / 10));
  for (long double reset : {0.1L, 0.3L, 0.9L}) {
    const auto gc = (1 - 0.15L) / reset * 0.2L;
    const auto average = (1 - 0.15L) / 0.15L * 0.5L;
    Near((gc / average) * reset * 256, 15.36L);
  }
  Near(Priority(0, 10), 1); Near(Priority(20, 10), 2); Near(Priority(21, 10), 1);
  rrzc_oaza::Zone z{10, 50, 100, 4, {{1, 10, 0}, {2, 40, 5}}};
  const auto literal = Cost(z, 10, CostInterpretation::kPrintedLastFileValidU);
  Near(literal, 1);
  std::reverse(z.parts.begin(), z.parts.end());
  Near(Cost(z, 10, CostInterpretation::kPrintedLastFileValidU), 2);
  const auto adapted = Cost(z, 10, CostInterpretation::kAdaptedByteWeightedValidCost);
  std::reverse(z.parts.begin(), z.parts.end());
  Near(Cost(z, 10, CostInterpretation::kAdaptedByteWeightedValidCost), adapted);
  rrzc_oaza::Zone sparse{11, 20, 100, 4, {{1, 5, 0}, {2, 15, 5}}};
  Near(Cost(sparse, 10, CostInterpretation::kPrintedLastFileValidU), 1.6L);
  Near(Cost(sparse, 10, CostInterpretation::kProseLastFileInvalidU), 0.4L);
  Near(Cost(sparse, 10, CostInterpretation::kAdaptedByteWeightedValidCost), 0.5L);
  rrzc_oaza::Zone dense{12, 80, 100, 4, {{2, 80, 5}}};
  assert(rrzc_oaza::Select({sparse, dense}, 1, 10, CostInterpretation::kPrintedLastFileValidU)[0] == 12);
  assert(rrzc_oaza::Select({sparse, dense}, 1, 10, CostInterpretation::kAdaptedByteWeightedValidCost)[0] == 11);

  using namespace rrzc_coldmap;
  AdaptiveDepth history;
  assert(history.AtCleaning(4) == 4 && history.AtCleaning(4) == 2 && history.AtCleaning(12) == 4);
  assert(history.AtCleaning(24) == 6); Invalid([&] { history.AtCleaning(1); });
  std::map<uint64_t, long double> cold{{1, 0}, {2, 1}};
  rrzc_coldmap::Zone together{10, 100, 200, {{1, 10}, {2, 90}}};
  auto split = together; split.parts = {{1, 3}, {1, 7}, {2, 40}, {2, 50}};
  Near(ZoneCold(together, cold), 0.9L); Near(ZoneCold(split, cold), 0.9L);
  Near(Score(together, cold), 0.9L);
  assert(rrzc_coldmap::Select({together, {20, 50, 200, {{1, 50}}}}, cold) == 10);
  auto unknown = together; unknown.parts[0].file = 99; Invalid([&] { ZoneCold(unknown, cold); });

  auto options = Config(); options.level_bytes[1] = 16000000;
  options.file_bytes[2] = 64000000; options.output_ratio[2] = 0.73L;
  std::vector<File> snapshot{F(1, 1, 1, 13, 64000000), F(2, 2, 1, 4, 16000000),
                              F(3, 2, 5, 9, 32000000), F(4, 2, 16, 22, 64000000)};
  const auto result = Simulate(snapshot, options, 1, FactorDirection::kArtifactColdMeansSurvival);
  assert(result.steps.size() == 1 && result.steps[0].removed.size() == 3);
  const auto& step = result.steps[0]; assert(step.created.size() == 2);
  assert(step.input_bytes == 112000000 && step.output_bytes == 81760000);
  assert(step.created[0].bytes == 64000000 && step.created[1].bytes == 17760000);
  assert(step.created[0].low == Key(1) && step.created[0].high == Key(10));
  assert(step.created[1].low == Key(11) && step.created[1].high == Key(13));
  Near(result.cold.at(2), result.cold.at(1)); Near(result.cold.at(3), result.cold.at(1));
  assert(snapshot.size() == 4 && snapshot[0].level == 1);  // Snapshot input is immutable.
  auto levels4 = options; levels4.levels = 4; levels4.level_bytes.push_back(1000000000);
  levels4.file_bytes.push_back(64000000); levels4.output_ratio.push_back(1);
  levels4.level_bytes[2] = 40000000;
  const auto future = Simulate(snapshot, levels4, 3, FactorDirection::kArtifactColdMeansSurvival);
  assert(future.steps.size() == 3);
  assert(future.steps[1].pivot > 4 || future.steps[2].pivot > 4);  // Generated SSTs participate later.
  auto busy = snapshot; for (auto& f : busy) f.compacting = true;
  assert(Simulate(busy, options, 100, FactorDirection::kPrintedEquation).steps.empty());
  auto intra_options = Config(); intra_options.l0_trigger = 2;
  std::vector<File> intra{F(1, 0, 1, 10, 20), F(2, 0, 5, 15, 20), F(3, 1, 1, 15, 10)};
  intra.back().compacting = true;
  const auto intra_result = Simulate(intra, intra_options, 1, FactorDirection::kPrintedEquation);
  assert(intra_result.steps.size() == 1 && intra_result.steps[0].intra_l0);
  assert(intra_result.steps[0].destination == 0 && intra_result.steps[0].removed.size() == 2);
  auto conflict_options = Config(); conflict_options.level_bytes[1] = 1;
  std::vector<File> conflict{F(1, 0, 1, 10, 10), F(2, 1, 20, 30, 20), F(3, 2, 20, 30, 10)};
  conflict.front().compacting = true;
  assert(Simulate(conflict, conflict_options, 1, FactorDirection::kPrintedEquation).steps.empty());
  auto malformed = snapshot; malformed.push_back(snapshot.front());
  Invalid([&] { Simulate(malformed, options, 1, FactorDirection::kPrintedEquation); });
  Near(Normalize(5, 0, 10), 0.5L); Near(Normalize(5, 5, 5), 0);
  // Direction conflict is visible, never silently corrected in Equation 1.
  auto mixed = std::vector<File>{F(1, 1, 1, 10, 20), F(2, 1, 20, 30, 40),
                                  F(3, 2, 1, 10, 10), F(4, 2, 20, 30, 10)};
  const auto printed = Initial(mixed, Config(), FactorDirection::kPrintedEquation);
  const auto survival = Initial(mixed, Config(), FactorDirection::kArtifactColdMeansSurvival);
  Near(printed.at(1) + survival.at(1), 1);
  assert(printed.at(2) != survival.at(2));
  std::cout << "paper quantity/placement ambiguities; byte-weighted coldness; adaptive K=3; "
               "synthetic output/range propagation; trivial/intra-L0/busy termination passed\n";
}
