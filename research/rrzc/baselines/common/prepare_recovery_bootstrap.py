"""Fork a frozen paper adapter and publish recovered metadata before open writes."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil


def sha(data):
    return hashlib.sha256(data).hexdigest()


def once(text, old, new):
    assert text.count(old) == 1, 'recovery anchor changed: ' + old[:100]
    return text.replace(old, new)


def recovery_code(method, publication):
    restore = ''
    if method == 'oaza_gc':
        restore = '''
        // A recovered MANIFEST does not supply trustworthy nonempty-key counts.
        // Pin the recovered Version, then read the original SST properties with
        // the DB mutex released. No guessed count or published partial frame.
        auto* recovered_version = cfd->current();
        recovered_version->Ref();
        impl->mutex_.Unlock();
        TablePropertiesCollection recovered_properties;
        const ReadOptions properties_read(Env::IOActivity::kDBOpen);
        s = recovered_version->GetPropertiesOfAllTables(properties_read, &recovered_properties);
        impl->mutex_.Lock();
        recovered_version->Unref();
        if (!s.ok()) break;
        for (const auto& item : recovered_properties) {
          const auto found = item.second->user_collected_properties.find("rrzc.oaza.nonempty.keys.v1");
          if (found == item.second->user_collected_properties.end()) {
            s = Status::NotSupported("OAZA SST lacks persisted nonempty-key count"); break;
          }
          size_t consumed = 0; const auto keys = std::stoull(found->second, &consumed);
          if (!keys || consumed != found->second.size() || keys != item.second->num_entries || item.second->num_deletions)
            throw std::invalid_argument("invalid persisted OAZA key count");
          rrzc_caza::Bridge().RestoreKeys(item.first, keys);
        }
        if (!s.ok()) break;
'''
    # This is the same committed-Version publication used during normal updates.
    # Keep the model options and metadata fields identical to the frozen adapter.
    for member in ('versions_', 'dbname_'):
        publication = publication.replace(member, 'impl->' + member)
    return '''
  // Related-work recovery bootstrap: the filesystem GC worker can need a
  // committed LSM frame while DB::Open waits for MANIFEST/CURRENT space.
  // Publishing after those writes creates a circular dependency under pressure.
  if (s.ok() && rrzc_caza::Enabled() && !recovery_ctx.is_new_db_) {
    try {
      for (auto* cfd : *impl->versions_->GetColumnFamilySet()) {
        if (cfd->GetName() != "default") {
          s = Status::NotSupported("related recovery supports the default CF only"); break;
        }
''' + restore + publication + '''
        ROCKS_LOG_INFO(impl->immutable_db_options_.info_log,
                      "Related recovery snapshot published before open writes");
      }
    } catch (const std::exception& error) {
      s = Status::Corruption("related recovery metadata", error.what());
    }
  }
'''


def prepare(source, output):
    assert not output.exists(), 'refuse overwrite'
    identity = json.loads((source / 'overlay_identity.json').read_text())
    method = identity['method']; assert method in ('oaza_gc', 'coldmap')
    files = {n: (source / 'src/rocksdb' / n).read_bytes() for n in identity['source_sha256']}
    assert all(sha(data) == identity['source_sha256'][n] for n, data in files.items())
    original = dict(files)
    name = 'db/db_impl/db_impl_compaction_flush.cc'
    text = files[name].decode()
    begin = text.index('  if (rrzc_caza::Enabled()) {', text.index('void DBImpl::InstallSuperVersionAndScheduleWork('))
    end = text.index('\n  EnqueuePendingCompaction(cfd);', begin)
    block = text[begin:end]
    assert block.endswith('\n  }')
    publication = block[len('  if (rrzc_caza::Enabled()) {\n'):-len('\n  }')]
    name = 'db/db_impl/db_impl_open.cc'; text = files[name].decode()
    if '#include "rrzc_caza_bridge.h"' not in text:
        text = '#include "rrzc_caza_bridge.h"\n' + text
    if method == 'oaza_gc':
        begin = text.index('  if (s.ok() && rrzc_caza::Enabled()) {\n    TablePropertiesCollection properties;')
        end = text.index('\n  if (s.ok()) {\n    *dbptr = std::move(impl);', begin)
        text = text[:begin] + text[end:]
    anchor = '                    &recovered_seq, &recovery_ctx, can_retry);\n'
    text = once(text, anchor, anchor + recovery_code(method, publication))
    files[name] = text.encode()
    name = 'include/rrzc_caza_bridge.h'
    text = once(files[name].decode(),
        '    if (live_.size() != 1) throw std::invalid_argument("ColdMap requires exactly one DB");',
        '    if (live_.empty()) throw std::invalid_argument("related LSM snapshot is not published yet");\n'
        '    if (live_.size() != 1) throw std::invalid_argument("related adapter requires exactly one DB");')
    files[name] = text.encode()
    output.mkdir(parents=True)
    for name, data in files.items():
        path = output / 'src/rocksdb' / name; path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as f: f.write(data)
        shutil.copymode(source / 'src/rocksdb' / name, path)
    changed = [n for n in files if files[n] != original[n]]
    patch = ''.join(''.join(difflib.unified_diff(original[n].decode().splitlines(True),
                    files[n].decode().splitlines(True), fromfile='a/' + n, tofile='b/' + n)) for n in changed)
    (output / 'recovery-bootstrap.patch').write_text(patch)
    identity.update(recovery_bootstrap=dict(source=str(source), source_identity_sha256=sha(
        (source / 'overlay_identity.json').read_bytes()), changed=changed,
        policy_formulas_unchanged=True, device_verified=False),
        source_sha256={n: sha(data) for n, data in files.items()},
        changed_units=list(dict.fromkeys([*identity['changed_units'], *changed])),
        implementation_complete=False, compiled=False, device_verified=False)
    (output / 'overlay_identity.json').write_text(json.dumps(identity, ensure_ascii=False, indent=2) + '\n')
    print(output, changed)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); prepare(a.source.resolve(), a.output.resolve())
