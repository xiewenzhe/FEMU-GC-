"""Create a distinct ColdMap overlay from the hash-frozen CAZA software substrate."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(BASE / 'caza'))
from prepare import once, change_function


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare(substrate, output):
    assert not output.exists(), 'refuse overwrite'
    source = substrate / 'src/rocksdb'
    identity = json.loads((substrate / 'overlay_identity.json').read_text())
    files = {n: (source / n).read_bytes() for n in identity['source_sha256']}
    assert all(sha(data) == identity['source_sha256'][n] for n, data in files.items())
    original = dict(files)
    policy = 'include/rrzc_caza_policy.h'
    files[policy] = once(files[policy].decode(), '  std::vector<ZoneId> zones;',
        '  std::vector<ZoneId> zones;\n  uint64_t bytes = 0, epoch = 0;\n  bool compacting = false;').encode()
    bridge = 'include/rrzc_caza_bridge.h'
    text = files[bridge].decode()
    text = '#include "rrzc_coldmap_policy.h"\n' + text
    text = once(text, 'namespace rrzc_caza {', '''namespace rrzc_caza {
inline bool ColdMapEnabled() {
  const char* mode = std::getenv("ZENFS_RELATED_WORK");
  return mode && std::string(mode) == "coldmap";
}
inline const char* ColdMapDirectionName() {
  const char* value = std::getenv("COLDMAP_FACTOR_DIRECTION");
  if (!value || (std::string(value) != "printed" && std::string(value) != "artifact-survival"))
    throw std::invalid_argument("explicit COLDMAP_FACTOR_DIRECTION=printed|artifact-survival required");
  return value;
}
inline rrzc_coldmap::FactorDirection ColdMapDirection() {
  return std::string(ColdMapDirectionName()) == "printed" ? rrzc_coldmap::FactorDirection::kPrintedEquation :
      rrzc_coldmap::FactorDirection::kArtifactColdMeansSurvival;
}''')
    text = once(text, 'if (std::string(mode) != "caza")', 'if (std::string(mode) != "coldmap")')
    text = once(text, '    return true;\n  }();', '    ColdMapDirection();\n    return true;\n  }();')
    text = once(text, 'struct Snapshot { uint64_t version = 0; std::vector<File> files; };',
        'struct Snapshot { uint64_t version = 0, compactions = 0; std::vector<File> files; rrzc_related::Options cold_options; };')
    text = once(text, '  void Publish(const std::string& db, std::vector<Live> files) {',
        '  void Publish(const std::string& db, std::vector<Live> files, rrzc_related::Options options) {')
    text = once(text, '    live_[Path(db)] = std::move(files);',
        '    live_[Path(db)] = std::move(files);\n    options_[Path(db)] = std::move(options);')
    text = once(text, '  Snapshot Capture(uint64_t device, const std::string& name) {', '''  void Mark(uint64_t file, bool compacting) {
    std::lock_guard<std::mutex> lock(mutex_); compacting_[file] = compacting; ++version_;
  }
  void Completed(int output_level, uint64_t input, uint64_t output) {
    std::lock_guard<std::mutex> lock(mutex_); ++compactions_;
    if (input && output) { ratios_[output_level].first += static_cast<long double>(output) / input; ++ratios_[output_level].second; }
    ++version_;
  }
  Snapshot Capture(uint64_t device, const std::string& name) {''')
    text = once(text, '    result.version = version_;', '''    result.version = version_; result.compactions = compactions_;
    if (live_.size() != 1) throw std::invalid_argument("ColdMap requires exactly one DB");
    result.cold_options = options_.at(Parent(Path(name)));
    for (const auto& ratio : ratios_) if (ratio.first >= 0 && ratio.first < result.cold_options.levels && ratio.second.second)
      result.cold_options.output_ratio[ratio.first] = ratio.second.first / ratio.second.second;''')
    text = once(text, '      auto file = live.file;', '''      auto file = live.file;
      if (compacting_.count(file.number)) file.compacting = compacting_.at(file.number);''')
    text = once(text, '  uint64_t version_ = 0;', '''  uint64_t version_ = 0, compactions_ = 0;
  std::map<std::string, rrzc_related::Options> options_;
  std::map<uint64_t, bool> compacting_;
  std::map<int, std::pair<long double, uint64_t>> ratios_;''')
    files[bridge] = text.encode()
    name = 'db/db_impl/db_impl_compaction_flush.cc'
    text = files[name].decode()
    text = once(text, '      file.smallest = item.smallestkey; file.largest = item.largestkey;',
        '      file.smallest = item.smallestkey; file.largest = item.largestkey;\n'
        '      file.bytes = item.size; file.epoch = item.epoch_number; file.compacting = item.being_compacted;')
    text = once(text, '    rrzc_caza::Bridge().Publish(dbname_, std::move(live));', '''    const auto& immutable = cfd->ioptions();
    const auto& mutable_options = cfd->GetLatestMutableCFOptions();
    if (immutable.compaction_style != kCompactionStyleLevel || immutable.compaction_pri != kMinOverlappingRatio)
      throw std::invalid_argument("ColdMap port requires leveled kMinOverlappingRatio");
    const auto* storage = cfd->current()->storage_info();
    rrzc_related::Options model;
    model.levels = immutable.num_levels; model.base_level = std::max(1, storage->base_level());
    model.l0_trigger = mutable_options.level0_file_num_compaction_trigger;
    model.level_bytes.resize(model.levels); model.file_bytes.resize(model.levels);
    model.output_ratio.assign(model.levels, 1);  // No observations yet: neutral output-size prior.
    for (int l = 0; l < model.levels; ++l) {
      model.level_bytes[l] = l ? std::max<uint64_t>(1, storage->MaxBytesForLevel(l)) : 1;
      model.file_bytes[l] = MaxFileSizeForLevel(mutable_options, l, immutable.compaction_style,
          model.base_level, immutable.level_compaction_dynamic_level_bytes);
    }
    rrzc_caza::Bridge().Publish(dbname_, std::move(live), std::move(model));''')
    text = change_function(text, 'void DBImpl::NotifyOnCompactionCompleted(', lambda part:
        once(part, '  if (immutable_db_options_.listeners.size() == 0U) {', '''  if (st.ok() && rrzc_caza::Enabled())
    rrzc_caza::Bridge().Completed(c->output_level(), compaction_job_stats.total_input_bytes,
                                 compaction_job_stats.total_output_bytes);
  if (immutable_db_options_.listeners.size() == 0U) {'''))
    text = change_function(text, 'Status DBImpl::CompactFilesImpl(', lambda part:
        once(part, '  if (status.ok()) {\n    assert(compaction_job.io_status().ok());', '''  if (status.ok() && rrzc_caza::Enabled())
    rrzc_caza::Bridge().Completed(c->output_level(), compaction_job_stats.total_input_bytes,
                                 compaction_job_stats.total_output_bytes);
  if (status.ok()) {
    assert(compaction_job.io_status().ok());'''))
    files[name] = text.encode()
    name = 'db/compaction/compaction.cc'
    text = '#include "rrzc_caza_bridge.h"\n' + files[name].decode()
    text = once(text, '      inputs_[i][j]->being_compacted = being_compacted;',
        '      inputs_[i][j]->being_compacted = being_compacted;\n'
        '      if (rrzc_caza::Enabled()) rrzc_caza::Bridge().Mark(inputs_[i][j]->fd.GetNumber(), being_compacted);')
    files[name] = text.encode()
    name = 'plugin/zenfs/fs/fs_zenfs.cc'
    text = files[name].decode()
    text = once(text, 'void ZenFS::GCWorker() {',
        'void ZenFS::GCWorker() {\n  rrzc_coldmap::AdaptiveDepth coldmap_depth;')
    text = once(text, '  uint64_t simple_space_weight = 50;',
        '  if (rrzc_caza::ColdMapEnabled()) simple_enabled = false;\n  uint64_t simple_space_weight = 50;')
    text = once(text, '    if (simple_enabled) {\n      const size_t original_count = migrate_zones_start.size();',
        '    #include "rrzc_coldmap_selector.inc"\n\n'
        '    if (simple_enabled) {\n      const size_t original_count = migrate_zones_start.size();')
    files[name] = text.encode()
    files['include/rrzc_related_snapshot.h'] = (BASE / 'common/rrzc_related_snapshot.h').read_bytes()
    files['include/rrzc_coldmap_policy.h'] = (HERE / 'policy.h').read_bytes()
    files['plugin/zenfs/fs/rrzc_coldmap_selector.inc'] = (HERE / 'selector.inc').read_bytes()
    output.mkdir(parents=True)
    for n, data in files.items():
        p = output / 'src/rocksdb' / n; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    changed = [n for n, data in files.items() if original.get(n) != data]
    patch = []
    for n in changed:
        patch.extend(difflib.unified_diff(original.get(n, b'').decode().splitlines(True), files[n].decode().splitlines(True),
            fromfile='a/' + n if n in original else '/dev/null', tofile='b/' + n))
    (output / 'coldmap.patch').write_text(''.join(patch))
    receipt = dict(identity, method='coldmap', substrate=str(substrate),
        substrate_identity_sha256=sha((substrate / 'overlay_identity.json').read_bytes()),
        source_sha256={n: sha(data) for n, data in files.items()}, changed_units=changed,
        implementation_complete=False, compiled=False, device_verified=False,
        restrictions=['single default CF DB, leveled kMinOverlappingRatio, fixed-width point keys',
                      'paper size-weighted aggregation and generated SST simulation; not artifact extent average',
                      'explicit factor direction required because paper Equation 1 and coldness semantics disagree',
                      'shared SST-only migration backend; WAL migration and universal compaction unsupported',
                      'shared GC trigger retained; not the paper FAR/concurrent experimental configuration',
                      'successful native completion notifications counted before the optional listener early return'])
    (output / 'overlay_identity.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(output, len(files), 'files', len(changed), 'changed')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--substrate', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); prepare(a.substrate.resolve(), a.output.resolve())
