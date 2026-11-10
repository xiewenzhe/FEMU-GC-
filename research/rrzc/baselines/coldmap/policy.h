// ColdMap ICS'26, Sections 5.1--5.3. Pure metadata model; no future trace inputs.
#pragma once
#include "rrzc_related_snapshot.h"
#include <deque>
#include <tuple>
namespace rrzc_coldmap {
using rrzc_related::File;
using rrzc_related::Options;
// Page 5 normalizes hotness upwards yet calls Equation 1 coldness. The original
// artifact complements both factors. Keep both readings explicit and testable.
enum class FactorDirection { kPrintedEquation, kArtifactColdMeansSurvival };
class AdaptiveDepth {
 public:
  uint64_t AtCleaning(uint64_t successful_compactions) {
    if (successful_compactions < previous_) throw std::invalid_argument("compaction counter regressed");
    counts_.push_back(successful_compactions - previous_); previous_ = successful_compactions;
    if (counts_.size() > 3) counts_.pop_front();
    rrzc_related::Wide total = 0;
    for (auto c : counts_) total += c;
    return static_cast<uint64_t>(total / counts_.size());
  }
 private:
  uint64_t previous_ = 0;
  std::deque<uint64_t> counts_;
};
inline int Next(int level, const Options& options) { return level == 0 ? options.base_level : level + 1; }
inline std::vector<long double> Scores(const std::vector<File>& files, const Options& options) {
  std::vector<long double> score(options.levels, 0);
  uint64_t l0 = 0;
  for (const auto& f : files) if (!f.wal && !f.compacting) {
    if (f.level == 0) ++l0;
    else score[f.level] += static_cast<long double>(f.bytes) / options.level_bytes[f.level];
  }
  score[0] = static_cast<long double>(l0) / options.l0_trigger;
  // Bottommost SSTs cannot initiate a downward leveled compaction.
  score.back() = 0;
  return score;
}
inline long double Horizontal(const File& pivot, const std::vector<File>& files, const Options& options) {
  if (pivot.level == options.levels - 1) return 0;
  long double lower = 0;
  for (const auto& f : files)
    if (!f.wal && f.level == Next(pivot.level, options) && rrzc_related::Overlap(pivot, f)) lower += f.bytes;
  return lower ? pivot.bytes / lower : std::numeric_limits<long double>::infinity();
}
inline long double Normalize(long double value, long double low, long double high) {
  if (!std::isfinite(value) || !std::isfinite(low) || !std::isfinite(high) || value < low || value > high)
    throw std::invalid_argument("invalid ColdMap normalization range");
  return high > low ? (value - low) / (high - low) : 0;
}
inline std::map<uint64_t, long double> Initial(const std::vector<File>& files, const Options& options,
                                            FactorDirection direction, long double alpha = 0.5L) {
  rrzc_related::Validate(files, options);
  if (!(alpha >= 0 && alpha <= 1)) throw std::invalid_argument("invalid ColdMap alpha");
  const auto scores = Scores(files, options);
  const auto bounds = std::minmax_element(scores.begin(), scores.end());
  std::map<int, std::pair<long double, long double>> horizontal_bounds;
  std::map<uint64_t, long double> h;
  for (const auto& f : files) if (!f.wal && !f.compacting) {
    const auto raw = Horizontal(f, files, options); h[f.number] = raw;
    if (!std::isfinite(raw) || f.level == options.levels - 1) continue;
    const auto found = horizontal_bounds.find(f.level);
    if (found == horizontal_bounds.end()) horizontal_bounds[f.level] = {raw, raw};
    else {
      found->second.first = std::min(found->second.first, raw);
      found->second.second = std::max(found->second.second, raw);
    }
  }
  std::map<uint64_t, long double> result;
  long double l0_cold = 0; uint64_t l0_bytes = 0;
  for (const auto& f : files) if (!f.wal) {
    long double cold = 0;
    if (f.compacting) cold = 0;
    else if (f.level == options.levels - 1 || !std::isfinite(h.at(f.number))) cold = 1;
    else {
      auto vf = Normalize(scores[f.level], *bounds.first, *bounds.second);
      const auto range = horizontal_bounds.at(f.level);
      auto hf = Normalize(h.at(f.number), range.first, range.second);
      if (direction == FactorDirection::kArtifactColdMeansSurvival) { vf = 1 - vf; hf = 1 - hf; }
      cold = alpha * vf + (1 - alpha) * hf;
    }
    result[f.number] = cold;
    if (f.level == 0) { l0_cold += cold * f.bytes; l0_bytes = rrzc_related::Add(l0_bytes, f.bytes); }
  }
  int next_level = -1;
  long double best = 1;  // Match the native size/count eligibility, not merely nonzero scores.
  for (int l = 0; l + 1 < options.levels; ++l)
    if (scores[l] >= best && (next_level < 0 || scores[l] > best)) { best = scores[l]; next_level = l; }
  for (const auto& f : files) if (f.wal)
    result[f.number] = next_level == 0 && l0_bytes ? l0_cold / l0_bytes : 0;
  return result;
}
struct Step {
  int source = 0, destination = 0;
  uint64_t pivot = 0, input_bytes = 0, output_bytes = 0;
  bool trivial = false, intra_l0 = false;
  std::vector<uint64_t> removed;
  std::vector<File> created;
};
struct Result {
  std::map<uint64_t, long double> cold;
  std::vector<File> predicted_files;
  std::vector<Step> steps;
};
inline Result Simulate(const std::vector<File>& input, const Options& options, uint64_t depth,
                       FactorDirection direction, long double alpha = 0.5L) {
  Result result; result.cold = Initial(input, options, direction, alpha);
  auto files = input;
  uint64_t next_id = 0;
  for (const auto& f : input) next_id = std::max(next_id, f.number);
  for (uint64_t iteration = 0; iteration < depth; ++iteration) {
    const auto scores = Scores(files, options);
    std::vector<int> levels;
    for (int l = 0; l + 1 < options.levels; ++l) if (scores[l] >= 1) levels.push_back(l);
    std::stable_sort(levels.begin(), levels.end(), [&](int a, int b) { return scores[a] > scores[b]; });
    bool progress = false;
    for (int level : levels) {
      if (level == options.base_level && std::any_of(files.begin(), files.end(),
          [](const auto& f) { return !f.wal && f.compacting && f.level == 0; })) continue;
      std::vector<File> candidates;
      for (const auto& f : files) if (!f.wal && !f.compacting && f.level == level) candidates.push_back(f);
      std::sort(candidates.begin(), candidates.end(), [&](const auto& a, const auto& b) {
        if (level == 0) return std::tie(a.epoch, a.number) < std::tie(b.epoch, b.number);
        const auto ah = Horizontal(a, files, options), bh = Horizontal(b, files, options);
        return ah != bh ? ah > bh : a.number < b.number;
      });
      for (const auto& pivot : candidates) {
        const int destination = Next(level, options);
        bool lower_busy = false;
        for (const auto& f : files) if (!f.wal && f.compacting && f.level == destination) lower_busy = true;
        const bool intra = level == 0 && lower_busy;
        if (lower_busy && !intra) continue;
        std::set<uint64_t> selected{pivot.number};
        File span = pivot;
        // L0 range expansion is transitive. Include next-level overlaps until
        // both source and destination ranges are closed, never just one pivot KR.
        bool grew = true, busy = false;
        while (grew) {
          grew = false;
          for (const auto& f : files) {
            const bool source_l0 = level == 0 && f.level == 0;
            if (f.wal || (!source_l0 && (intra || f.level != destination)) || selected.count(f.number) ||
                !rrzc_related::Overlap(span, f)) continue;
            if (f.compacting) { busy = true; continue; }
            selected.insert(f.number); span.low = std::min(span.low, f.low); span.high = std::max(span.high, f.high);
            grew = true;
          }
        }
        if (busy || (intra && selected.size() < 2)) continue;
        Step step; step.source = level; step.destination = intra ? 0 : destination;
        step.pivot = pivot.number; step.intra_l0 = intra;
        for (const auto& f : files) if (selected.count(f.number)) {
          step.input_bytes = rrzc_related::Add(step.input_bytes, f.bytes); step.removed.push_back(f.number);
        }
        step.trivial = selected.size() == 1 && !intra;
        if (step.trivial) {
          for (auto& f : files) if (f.number == pivot.number) f.level = destination;
          result.cold[pivot.number] = 1; step.output_bytes = pivot.bytes;
        } else {
          for (auto id : selected) if (id != pivot.number) result.cold[id] = result.cold.at(pivot.number);
          const long double predicted = step.input_bytes * options.output_ratio[step.destination];
          if (!(predicted >= 1) || predicted >= static_cast<long double>(UINT64_MAX))
            throw std::overflow_error("invalid predicted compaction size");
          step.output_bytes = static_cast<uint64_t>(predicted);
          files.erase(std::remove_if(files.begin(), files.end(), [&](const auto& f) { return selected.count(f.number); }), files.end());
          if (span.low.size() != span.high.size()) throw std::invalid_argument("prediction requires fixed-width keys");
          const auto low = rrzc_related::KeyInteger(span.low), high = rrzc_related::KeyInteger(span.high);
          if (high - low == static_cast<rrzc_related::Wide>(-1)) throw std::overflow_error("full 128-bit key domain");
          const auto width = high - low + 1;
          const uint64_t maximum = options.file_bytes[step.destination];
          if (!maximum) throw std::invalid_argument("missing predicted output SST bound");
          const uint64_t count = step.output_bytes / maximum + (step.output_bytes % maximum != 0);
          if (count > width) throw std::invalid_argument("more synthetic SSTs than fixed-width keys");
          for (uint64_t offset = 0; offset < step.output_bytes;) {
            const auto bytes = std::min(maximum, step.output_bytes - offset);
            if (next_id == UINT64_MAX) throw std::overflow_error("synthetic SST identity exhausted");
            File created; created.number = ++next_id; created.level = step.destination; created.bytes = bytes;
            created.epoch = pivot.epoch;
            const auto first = rrzc_related::Fraction(width, offset, step.output_bytes);
            const auto end = rrzc_related::Fraction(width, offset + bytes, step.output_bytes);
            if (end <= first) throw std::invalid_argument("zero-width synthetic key range");
            created.low = rrzc_related::KeyString(low + first, span.low.size());
            created.high = rrzc_related::KeyString(low + end - 1, span.low.size());
            files.push_back(created); step.created.push_back(created); offset += bytes;
          }
          const auto fresh = Initial(files, options, direction, alpha);
          for (const auto& f : step.created) result.cold[f.number] = fresh.at(f.number);
        }
        result.steps.push_back(std::move(step)); progress = true; break;
      }
      if (progress) break;
    }
    if (!progress) break;  // Every successful iteration removes inputs or moves a file down.
  }
  result.predicted_files = std::move(files);
  return result;
}
struct Part { uint64_t file, bytes; };
struct Zone { uint64_t start, valid, capacity; std::vector<Part> parts; };
inline long double ZoneCold(const Zone& zone, const std::map<uint64_t, long double>& cold) {
  uint64_t total = 0; long double weighted = 0;
  for (const auto& part : zone.parts) {
    const auto found = cold.find(part.file);
    if (found == cold.end() || !(found->second >= 0 && found->second <= 1))
      throw std::invalid_argument("Zone has unknown/invalid ColdMap file");
    total = rrzc_related::Add(total, part.bytes); weighted += found->second * part.bytes;
  }
  if (!total || total != zone.valid || zone.valid > zone.capacity)
    throw std::invalid_argument("incomplete ColdMap Zone coverage");
  return weighted / total;  // Equation 2: bytes, not file count or extent count.
}
inline long double Score(const Zone& zone, const std::map<uint64_t, long double>& cold) {
  if (!zone.valid || !zone.capacity || zone.valid >= zone.capacity)
    throw std::invalid_argument("ColdMap victim must reclaim positive bytes");
  return static_cast<long double>(zone.capacity - zone.valid) * ZoneCold(zone, cold) / zone.valid;
}
inline uint64_t Select(const std::vector<Zone>& zones, const std::map<uint64_t, long double>& cold) {
  if (zones.empty()) throw std::invalid_argument("no eligible ColdMap Zone");
  std::set<uint64_t> seen;
  uint64_t best = zones.front().start; long double score = -1;
  for (const auto& zone : zones) {
    if (!seen.insert(zone.start).second) throw std::invalid_argument("duplicate ColdMap Zone");
    const auto current = Score(zone, cold);
    if (current > score || (current == score && zone.start < best)) { score = current; best = zone.start; }
  }
  return best;
}
}  // namespace rrzc_coldmap
