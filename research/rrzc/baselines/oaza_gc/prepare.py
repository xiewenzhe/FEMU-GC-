"""Generate an isolated OAZA-GC overlay, with explicit literal/adapted selection."""
import argparse
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
spec = importlib.util.spec_from_file_location('coldmap_generator', BASE / 'coldmap/prepare.py')
cold = importlib.util.module_from_spec(spec); spec.loader.exec_module(cold)
once, change_function = cold.once, cold.change_function


def prepare(substrate, output):
    assert not output.exists(), 'refuse overwrite'
    # Share only verified metadata-transport plumbing, never a ColdMap score or
    # its generated selector. No original/CAZA tree is edited by this generator.
    with tempfile.TemporaryDirectory(prefix='rrzc-oaza-substrate-') as temporary:
        staging = Path(temporary) / 'source'
        cold.prepare(substrate, staging)
        identity = json.loads((staging / 'overlay_identity.json').read_text())
        files = {n: (staging / 'src/rocksdb' / n).read_bytes() for n in identity['source_sha256']}
    original_identity = json.loads((substrate / 'overlay_identity.json').read_text())
    original = {n: (substrate / 'src/rocksdb' / n).read_bytes() for n in original_identity['source_sha256']}
    assert all(cold.sha(data) == original_identity['source_sha256'][n] for n, data in original.items())
    p = 'include/rrzc_caza_policy.h'
    text = files[p].decode()
    text = once(text, '  uint64_t bytes = 0, epoch = 0;', '  uint64_t bytes = 0, epoch = 0, keys = 0;')
    text = once(text, 'struct Request {\n  uint64_t number = 0;',
        'struct Request {\n  uint64_t keys = 0, bytes = 0;\n  uint64_t number = 0;')
    files[p] = text.encode()
    p = 'include/rrzc_caza_bridge.h'
    text = files[p].decode().replace('#include "rrzc_coldmap_policy.h"', '#include "rrzc_oaza_policy.h"')
    start = text.index('inline bool ColdMapEnabled()'); end = text.index('inline bool Enabled()', start)
    text = text[:start] + '''inline bool OazaEnabled() {
  const char* value = std::getenv("ZENFS_RELATED_WORK"); return value && std::string(value) == "oaza_gc";
}
inline const char* OazaVariantName() {
  const char* v = std::getenv("OAZA_GC_VARIANT");
  if (!v || (std::string(v) != "literal" && std::string(v) != "adapted" && std::string(v) != "prose"))
    throw std::invalid_argument("explicit OAZA_GC_VARIANT=literal|adapted|prose required");
  return v;
}
inline rrzc_oaza::CostInterpretation OazaVariant() {
  const std::string v = OazaVariantName();
  return v == "literal" ? rrzc_oaza::CostInterpretation::kPrintedLastFileValidU :
      v == "prose" ? rrzc_oaza::CostInterpretation::kProseLastFileInvalidU :
      rrzc_oaza::CostInterpretation::kAdaptedByteWeightedValidCost;
}
''' + text[end:]
    text = text.replace('std::string(mode) != "coldmap"', 'std::string(mode) != "oaza_gc"').replace('    ColdMapDirection();', '    OazaVariant();')
    text = once(text, 'rrzc_related::Options cold_options; };', 'rrzc_related::Options cold_options; long double amplification = 10; };')
    text = once(text, 'class Registry {', '''inline std::vector<rrzc_related::File> ModelFiles(const std::vector<File>& files) {
  std::vector<rrzc_related::File> result;
  for (const auto& f : files) {
    rrzc_related::File item; item.number = f.number; item.level = f.level;
    item.low = f.smallest; item.high = f.largest; item.bytes = f.bytes; item.keys = f.keys;
    result.push_back(std::move(item));
  }
  return result;
}
class Registry {''')
    text = once(text, 'void Ready(const std::string& name, int level, const std::string& low, const std::string& high)',
        'void Ready(const std::string& name, int level, const std::string& low, const std::string& high, uint64_t keys, uint64_t bytes)')
    text = once(text, '    Request request{found->second->request.number, level, low, high};',
        '    Request request; request.number = found->second->request.number; request.level = level;\n'
        '    request.smallest = low; request.largest = high; request.keys = keys; request.bytes = bytes;\n'
        '    key_counts_[request.number] = keys;')
    text = once(text, 'void Publish(const std::string& db, std::vector<Live> files, rrzc_related::Options options)',
        'void Publish(const std::string& db, std::vector<Live> files, rrzc_related::Options options, long double amplification)')
    text = once(text, '    options_[Path(db)] = std::move(options);',
        '    options_[Path(db)] = std::move(options); amplification_ = amplification;')
    text = once(text, '  void Mark(uint64_t file, bool compacting) {', '''  void Associate(uint64_t file, int level) {
    std::lock_guard<std::mutex> lock(mutex_); associated_[file] = level;
  }
  int Association(uint64_t file, int fallback) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = associated_.find(file); return it == associated_.end() ? fallback : it->second;
  }
  void RestoreKeys(const std::string& path, uint64_t keys) {
    if (!keys) throw std::invalid_argument("OAZA requires a verified nonempty-key count");
    std::lock_guard<std::mutex> lock(mutex_); key_counts_[Number(path)] = keys; ++version_;
  }
  void Mark(uint64_t file, bool compacting) {''')
    text = once(text, '    result.version = version_; result.compactions = compactions_;',
        '    result.version = version_; result.compactions = compactions_; result.amplification = amplification_;')
    text = once(text, '      auto file = live.file;',
        '      auto file = live.file;\n      if (key_counts_.count(file.number)) file.keys = key_counts_.at(file.number);\n'
        '      if (!file.keys) throw std::invalid_argument("OAZA missing restored key-count metadata");')
    text = once(text, '  uint64_t version_ = 0, compactions_ = 0;', '''  uint64_t version_ = 0, compactions_ = 0;
  long double amplification_ = 10;
  std::map<uint64_t, uint64_t> key_counts_;
  std::map<uint64_t, int> associated_;''')
    files[p] = text.encode()
    p = 'table/block_based/block_based_table_builder.cc'
    text = files[p].decode()
    text = once(text, '  int caza_level = -1;', '  int caza_level = -1;\n  uint64_t oaza_keys = 0;')
    text = once(text, '    const std::string user = ExtractUserKey(ikey).ToString();', '''    if (value_type != kTypeValue || value.empty()) {
      r->SetStatus(Status::NotSupported("OAZA adapter currently accepts nonempty point values only")); return;
    }
    ++r->oaza_keys;
    const std::string user = ExtractUserKey(ikey).ToString();''')
    text = once(text, 'r->caza_smallest, r->caza_largest);',
        'r->caza_smallest, r->caza_largest, r->oaza_keys, r->offset);')
    text = once(text, '    property_block_builder.AddTableProperty(rep_->props);',
        '    property_block_builder.AddTableProperty(rep_->props);\n'
        '    if (rrzc_caza::Enabled()) property_block_builder.Add("rrzc.oaza.nonempty.keys.v1", std::to_string(rep_->oaza_keys));')
    files[p] = text.encode()
    p = 'plugin/zenfs/fs/rrzc_caza_writable.inc'
    text = files[p].decode()
    text = once(text, '    context.request = request;', '''    request.bytes = buffered ? rrzc_related::PhysicalBound(caza_bytes_, buffer_sz,
        zoneFile_->IsSparse() ? ZoneFile::SPARSE_HEADER_SIZE : 0, block_sz) : caza_bytes_;
    context.request = request;''')
    text = once(text, '    if (caza_error_.ok()) {\n      caza_drained_ = true;', '''    if (caza_error_.ok()) {
      const int associated = rrzc_caza::Bridge().Association(request.number, request.level);
      if (associated != request.level) {
        const int label = std::min(5, 3 + std::max(0, associated - context.snapshot.cold_options.base_level));
        caza_error_ = zoneFile_->SetWriteLifeTimeHint(static_cast<Env::WriteLifeTimeHint>(label));
        if (!caza_error_.ok()) return caza_error_;
      }
      caza_drained_ = true;''')
    files[p] = text.encode()
    p = 'db/db_impl/db_impl_open.cc'
    text = '#include "rrzc_caza_bridge.h"\n' + files[p].decode()
    text = once(text, '  impl->options_mutex_.Unlock();\n  if (s.ok()) {\n    *dbptr = std::move(impl);', '''  impl->options_mutex_.Unlock();
  if (s.ok() && rrzc_caza::Enabled()) {
    TablePropertiesCollection properties;
    s = impl->GetPropertiesOfAllTables(&properties);
    if (s.ok()) for (const auto& item : properties) {
      const auto found = item.second->user_collected_properties.find("rrzc.oaza.nonempty.keys.v1");
      if (found == item.second->user_collected_properties.end()) {
        s = Status::NotSupported("OAZA SST lacks persisted nonempty-key count"); break;
      }
      try {
        size_t consumed = 0; const auto keys = std::stoull(found->second, &consumed);
        if (!keys || consumed != found->second.size() || keys != item.second->num_entries || item.second->num_deletions)
          throw std::invalid_argument("invalid persisted OAZA key count");
        rrzc_caza::Bridge().RestoreKeys(item.first, keys);
      } catch (const std::exception& error) { s = Status::Corruption(error.what()); break; }
    }
  }
  if (s.ok()) {
    *dbptr = std::move(impl);''')
    files[p] = text.encode()
    p = 'db/db_impl/db_impl_compaction_flush.cc'
    text = files[p].decode()
    text = once(text, '      file.bytes = item.size; file.epoch = item.epoch_number; file.compacting = item.being_compacted;',
        '      file.bytes = item.size; file.epoch = item.epoch_number; file.compacting = item.being_compacted;\n'
        '      file.keys = item.num_entries >= item.num_deletions ? item.num_entries - item.num_deletions : 0;')
    text = text.replace('"ColdMap port requires leveled kMinOverlappingRatio"', '"OAZA adapter requires leveled kMinOverlappingRatio"')
    text = once(text, 'rrzc_caza::Bridge().Publish(dbname_, std::move(live), std::move(model));',
        'rrzc_caza::Bridge().Publish(dbname_, std::move(live), std::move(model), mutable_options.max_bytes_for_level_multiplier);')
    files[p] = text.encode()
    p = 'plugin/zenfs/fs/fs_zenfs.cc'
    text = files[p].decode().replace('  rrzc_coldmap::AdaptiveDepth coldmap_depth;\n', '')
    text = text.replace('rrzc_caza::ColdMapEnabled()', 'rrzc_caza::OazaEnabled()')
    text = text.replace('#include "rrzc_coldmap_selector.inc"', '#include "rrzc_oaza_selector.inc"')
    # OAZA's threshold is part of its algorithm, isolated from the original 20%.
    text = once(text, '    if (free_percent > GC_START_LEVEL) continue;',
        '    if (free_percent > (rrzc_caza::OazaEnabled() ? 15 : GC_START_LEVEL)) continue;')
    files[p] = text.encode()
    files['plugin/zenfs/fs/rrzc_caza_zenfs.inc'] = (HERE / 'allocator.inc').read_bytes()
    p = 'db/compaction/compaction_job.cc'
    text = '#include "rrzc_caza_bridge.h"\n' + files[p].decode()
    text = once(text, '  auto manifest_wcb = [&compaction, &compaction_released](const Status& s) {', '''  if (rrzc_caza::Enabled()) {
    // Revalidate against the current committed Version while the DB mutex is held.
    // A concurrent lower-level insertion cannot turn a stale zero-overlap estimate
    // into overlapping level files. Other outputs in this same edit are checked too.
    auto& additions = edit->GetMutableNewFiles();
    auto* current_storage = compaction->column_family_data()->current()->storage_info();
    const auto* comparator = compaction->column_family_data()->ioptions().user_comparator;
    for (auto& item : additions) {
      const int desired = rrzc_caza::Bridge().Association(item.second.fd.GetNumber(), item.first);
      if (desired != item.first + 1 || item.first < 2 || desired >= current_storage->num_levels()) continue;
      const auto low = item.second.smallest.user_key(), high = item.second.largest.user_key();
      bool conflict = current_storage->OverlapInLevel(desired, &low, &high);
      for (const auto& other : additions) if (other.second.fd.GetNumber() != item.second.fd.GetNumber() && other.first == desired)
        conflict = conflict || !(comparator->Compare(other.second.largest.user_key(), low) < 0 ||
                                  comparator->Compare(high, other.second.smallest.user_key()) < 0);
      if (conflict) return Status::TryAgain("OAZA promotion conflicts with current committed range");
      item.first = desired;
    }
  }
  auto manifest_wcb = [&compaction, &compaction_released](const Status& s) {''')
    files[p] = text.encode()
    del files['include/rrzc_coldmap_policy.h']
    del files['plugin/zenfs/fs/rrzc_coldmap_selector.inc']
    files['include/rrzc_oaza_policy.h'] = (HERE / 'policy.h').read_bytes()
    files['plugin/zenfs/fs/rrzc_oaza_selector.inc'] = (HERE / 'selector.inc').read_bytes()
    output.mkdir(parents=True)
    for n, data in files.items():
        p = output / 'src/rocksdb' / n; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    changed = [n for n, data in files.items() if original.get(n) != data]
    patch = []
    for n in changed:
        patch.extend(difflib.unified_diff(original.get(n, b'').decode().splitlines(True), files[n].decode().splitlines(True),
            fromfile='a/' + n if n in original else '/dev/null', tofile='b/' + n))
    (output / 'oaza_gc.patch').write_text(''.join(patch))
    receipt = dict(original_identity, method='oaza_gc', substrate=str(substrate),
        source_sha256={n: cold.sha(data) for n, data in files.items()}, changed_units=changed,
        implementation_complete=False, compiled=False, device_verified=False,
        restrictions=['single default CF DB, fixed-width nonempty point values; deletes/empty dummy values rejected',
            'literal/prose last file ordered by file number; adapted uses byte-weighted priority times valid fraction',
            'quantity integer floor; allocated invalid bytes from written minus live, excluding unwritten tails',
            'zero-overlap promotion revalidated against committed Version before actual manifest level change',
            'nonempty-key counts persisted as optional SST user property; unmarked old SSTs explicitly rejected',
            'whole-file capacity includes native sparse headers and block padding; promoted file hint persisted',
            'after remount an unset Zone hint is rebuilt from the maximum live-file hint; mixed-hint rule is explicit adaptation',
            'native lifetime hint encoding 3/4/5 limits distinguishable deep-level lifetime classes',
            'shared SST-only migration backend and safety coverage/reserve constraints'])
    (output / 'overlay_identity.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(output, len(files), 'files', len(changed), 'changed')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--substrate', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); prepare(a.substrate.resolve(), a.output.resolve())
