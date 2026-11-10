"""Fork the recovery adapter and finish one selected source before the next."""
import argparse
import difflib
import json
from pathlib import Path
import shutil
from prepare_recovery_bootstrap import sha, once


def prepare(source, output):
    assert not output.exists()
    identity = json.loads((source / 'overlay_identity.json').read_text())
    assert identity['method'] == 'oaza_gc' and identity['recovery_bootstrap']['policy_formulas_unchanged']
    files = {n: (source / 'src/rocksdb' / n).read_bytes() for n in identity['source_sha256']}
    assert all(sha(data) == identity['source_sha256'][n] for n, data in files.items())
    original = dict(files)
    name = 'plugin/zenfs/fs/fs_zenfs.cc'; text = files[name].decode()
    begin = text.index('  for (const auto& it : file_extents) {', text.index('IOStatus ZenFS::MigrateExtents('))
    end = text.index('  for (uint64_t start : source_zones)', begin)
    old = text[begin:end]
    assert old == '''  for (const auto& it : file_extents) {
    s = MigrateFileExtents(it.first, it.second, stats);
    if (!s.ok()) break;
    s = zbd_->ResetUnusedIOZones();
    if (!s.ok()) break;
  }
'''
    new = '''  if (rrzc_caza::OazaEnabled()) {
    // OAZA can select several sources. A per-source capacity check cannot
    // fund filename-interleaved copies from every source before any Reset.
    // Keep exactly the selected extents; clear one source before the next.
    for (const auto& batch : rrzc_oaza::SourceBatches(file_extents)) {
      Info(logger_, "OAZA-GC source batch: zone=%lu files=%zu selected_set_unchanged=1",
           batch.first, batch.second.size());
      for (const auto& it : batch.second) {
        s = MigrateFileExtents(it.first, it.second, stats);
        if (!s.ok()) break;
        s = zbd_->ResetUnusedIOZones();
        if (!s.ok()) break;
      }
      if (!s.ok()) break;
    }
  } else {
''' + ''.join('  ' + line if line.strip() else line for line in old.splitlines(True)) + '''  }
'''
    text = text[:begin] + new + text[end:]
    files[name] = ('#include "rrzc_oaza_migration_order.h"\n' + text).encode()
    files['include/rrzc_oaza_migration_order.h'] = (Path(__file__).resolve().parents[1] / 'oaza_gc/migration_order.h').read_bytes()
    output.mkdir(parents=True)
    for name, data in files.items():
        path = output / 'src/rocksdb' / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        if name in original: shutil.copymode(source / 'src/rocksdb' / name, path)
    changed = [n for n in files if files[n] != original.get(n)]
    patch = ''.join(''.join(difflib.unified_diff(original.get(n, b'').decode().splitlines(True),
        files[n].decode().splitlines(True), fromfile='a/' + n, tofile='b/' + n)) for n in changed)
    (output / 'migration-batch-order.patch').write_text(patch)
    identity.update(migration_batch_execution=dict(source=str(source), changed=changed,
        selected_set_and_cost_unchanged=True, order='source Zone address then filename',
        caveat='files spanning sources can have multiple mapping commits; not a score-policy change'),
        source_sha256={n: sha(data) for n, data in files.items()},
        changed_units=list(dict.fromkeys([*identity['changed_units'], *changed])),
        implementation_complete=False, compiled=False, device_verified=False)
    (output / 'overlay_identity.json').write_text(json.dumps(identity, ensure_ascii=False, indent=2) + '\n')
    print(output, changed)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); a = p.parse_args(); prepare(a.source.resolve(), a.output.resolve())
