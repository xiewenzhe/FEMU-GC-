"""CAZA native source syntax plus standalone policy/bridge/replay checks; no VM or device."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(overlay, header_root, output, sanitize_only=False):
    identity = json.loads((overlay / 'overlay_identity.json').read_text())
    source = overlay / 'src/rocksdb'
    assert not output.exists(), 'do not overwrite check evidence'
    assert all(digest(source / n) == h for n, h in identity['source_sha256'].items())
    dependency = json.loads((header_root / 'source.json').read_text())
    assert digest(header_root / 'libzbd/zbd.h') == dependency['sha256']
    materials = {p.name: digest(p) for p in HERE.iterdir()
                 if p.is_file() and p.suffix in ('.h', '.inc', '.cc', '.py')}
    output.mkdir(parents=True)
    results = []

    def run(argv, name, env=None):
        (output / (name + '.command.json')).write_text(json.dumps(argv) + '\n')
        with (output / (name + '.log')).open('x') as stream:
            done = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=300,
                                  cwd=source, env=env)
        results.append(dict(name=name, exit_code=done.returncode))
        print(name + ': ' + str(done.returncode), flush=True)
        return done.returncode == 0

    units = ['plugin/zenfs/fs/' + n + '.cc' for n in
             ('fs_zenfs', 'io_zenfs', 'zbd_zenfs', 'zbdlib_zenfs', 'zonefs_zenfs')]
    units += ['table/block_based/block_based_table_builder.cc',
              'db/db_impl/db_impl_compaction_flush.cc', 'db/flush_job.cc']
    for unit in ([] if sanitize_only else units + [str(HERE / 'probe.cc')]):
        flags = ['g++', '-std=c++20', '-fno-rtti', '-fsyntax-only', '-Wall', '-Wextra', '-Wshadow',
                 '-Werror', '-Wno-unused-parameter', '-DOS_LINUX', '-DROCKSDB_PLATFORM_POSIX',
                 '-DROCKSDB_LIB_IO_POSIX', '-DHAVE_UINT128_EXTENSION',
                 '-I' + str(header_root), '-I' + str(source / 'include'), '-I' + str(source),
                 '-I' + str(source / 'plugin/zenfs/fs')]
        run([*flags, str(source / unit)], 'syntax-' + Path(unit).stem)
    for name in ('policy', 'bridge', 'writable', 'allocator'):
        binary = output / (name + '-test')
        command = ['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
                   '-I' + str(source / 'include'), '-I' + str(HERE),
                   str(HERE / (name + '_test.cc')), '-o', str(binary)]
        if sanitize_only:
            command += ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        if run(command, 'compile-' + name):
            env = dict(os.environ, ZENFS_RELATED_WORK='caza')
            run([str(binary)], 'run-' + name, env)
    unchanged = all(digest(source / n) == h for n, h in identity['source_sha256'].items())
    tests_unchanged = all(digest(HERE / n) == h for n, h in materials.items())
    report = dict(passed=unchanged and tests_unchanged and all(row['exit_code'] == 0 for row in results),
                  results=results, source_unchanged=unchanged,
                  test_materials_unchanged=tests_unchanged, test_materials_sha256=materials,
                  overlay_identity_sha256=digest(overlay / 'overlay_identity.json'),
                  test_sources_sha256={str(p.name): digest(p) for p in HERE.glob('*test.cc')},
                  dependency=dependency,
                  sanitizers=['address', 'undefined'] if sanitize_only else [],
                  scope=('Standalone fake-I/O tests only' if sanitize_only else
                         'Host syntax without optional compression providers and standalone fake-I/O tests only'),
                  configured_guest_build=False, device_verified=False, performance_experiment=False)
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--overlay', type=Path, required=True)
    parser.add_argument('--header-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sanitize-only', action='store_true')
    args = parser.parse_args()
    raise SystemExit(0 if check(args.overlay.resolve(), args.header_root.resolve(),
                               args.output.resolve(), args.sanitize_only) else 1)
