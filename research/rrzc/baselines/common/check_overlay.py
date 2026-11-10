"""Syntax-check all affected native units and the new metadata bridge; no device I/O."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

BASE = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(overlay, output):
    assert not output.exists(), 'refuse overwrite'
    identity = json.loads((overlay / 'overlay_identity.json').read_text())
    root = overlay / 'src/rocksdb'
    assert all(digest(root / n) == h for n, h in identity['source_sha256'].items())
    output.mkdir(parents=True)
    units = ['plugin/zenfs/fs/' + n + '.cc' for n in
             ('fs_zenfs', 'io_zenfs', 'zbd_zenfs', 'zbdlib_zenfs', 'zonefs_zenfs')]
    units += ['table/block_based/block_based_table_builder.cc',
              'db/db_impl/db_impl_compaction_flush.cc', 'db/flush_job.cc']
    units += [n for n in identity['changed_units'] if n.endswith('.cc') and n not in units]
    flags = ['g++', '-std=c++20', '-fno-rtti', '-fsyntax-only', '-Wall', '-Wextra', '-Wshadow',
             '-Werror', '-Wno-unused-parameter', '-DOS_LINUX', '-DROCKSDB_PLATFORM_POSIX',
             '-DROCKSDB_LIB_IO_POSIX', '-DHAVE_UINT128_EXTENSION', '-I' + str(BASE / 'materials/libzbd-syntax-v1'),
             '-I' + str(root / 'include'), '-I' + str(root), '-I' + str(root / 'plugin/zenfs/fs')]
    results = []

    def run(command, name):
        (output / (name + '.command.json')).write_text(json.dumps(command) + '\n')
        with (output / (name + '.log')).open('x') as log:
            p = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        results.append(dict(name=name, exit_code=p.returncode))
        print(name, p.returncode, flush=True)
        return p.returncode == 0

    for unit in units + [str(BASE / 'caza/probe.cc')]:
        run(flags + [str(root / unit)], 'syntax-' + Path(unit).stem)
    binary = output / 'bridge-test'
    method = identity['method']
    if (BASE / method / 'bridge_test.cc').exists():
        command = ['g++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-fsanitize=undefined', '-fno-sanitize-recover=all', '-pthread',
                   '-I' + str(root / 'include'), str(BASE / method / 'bridge_test.cc'), '-o', str(binary)]
        if run(command, 'compile-bridge'): run([str(binary)], 'run-bridge')
    if (BASE / method / 'allocator_test.cc').exists():
        binary = output / 'allocator-test'
        command = ['g++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                   '-fsanitize=undefined', '-fno-sanitize-recover=all', '-pthread',
                   '-I' + str(root / 'include'), '-I' + str(BASE / method),
                   str(BASE / method / 'allocator_test.cc'), '-o', str(binary)]
        if run(command, 'compile-allocator'):
            variant = dict(ZENFS_RELATED_WORK=method, OAZA_GC_VARIANT='literal')
            # run() inherits the environment; use env(1) only for this standalone test.
            run(['env', *[k + '=' + v for k, v in variant.items()], str(binary)], 'run-allocator')
    unchanged = all(digest(root / n) == h for n, h in identity['source_sha256'].items())
    report = dict(passed=unchanged and all(r['exit_code'] == 0 for r in results), results=results,
                  source_unchanged=unchanged, configured_guest_build=False, device_verified=False)
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    return report['passed']


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--overlay', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); raise SystemExit(0 if check(a.overlay.resolve(), a.output.resolve()) else 1)
