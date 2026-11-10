"""Compile all affected CAZA units against verified matching headers, preserving the original library."""
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(target):
    root = Path('/home/femu/rocksdb')
    identity = json.loads((target / 'overlay_identity.json').read_text())
    src = target / 'src/rocksdb'
    for name, expected in identity['source_sha256'].items():
        assert digest(src / name) == expected, name
    library = root / 'librocksdb_debug.a'
    before = digest(library)
    assert before == identity['original_library_sha256']
    objects = target / 'objects'
    objects.mkdir()
    original = json.loads((target / 'compile.original.json').read_text())
    flags = original[original.index('-fno-rtti'):original.index('-c')]
    flags = [x for x in flags if x != '-g']
    # Header precedence is common to every new translation unit and the client.
    command = ['g++', '-I' + str(src / 'include'), '-I' + str(src),
               '-I' + str(src / 'plugin/zenfs/fs'), *flags, '-g0', '-DNDEBUG']

    def run(argv, name, env=None):
        (target / (name + '.command.json')).write_text(json.dumps(argv) + '\n')
        with (target / (name + '.log')).open('x') as output:
            subprocess.run(argv, cwd=root, stdout=output, stderr=subprocess.STDOUT,
                           timeout=900, check=True, env=env)
        print(name + ' passed', flush=True)

    units = ['plugin/zenfs/fs/' + name + '.cc' for name in
             ('fs_zenfs', 'io_zenfs', 'zbd_zenfs', 'zbdlib_zenfs', 'zonefs_zenfs')]
    units += ['table/block_based/block_based_table_builder.cc',
              'db/db_impl/db_impl_compaction_flush.cc', 'db/flush_job.cc']
    built = []
    for unit in units:
        name = Path(unit).stem
        obj = objects / (name + '.o')
        run([*command, '-c', str(src / unit), '-o', str(obj)], 'compile-' + name)
        built.append(str(obj))
    # The archive's core was built with NDEBUG, despite its *_debug.a name.
    # A public make dry-run is not proof of the existing archive's build flags.
    original_obj = objects / 'original-db_impl_compaction_flush.o'
    with original_obj.open('xb') as out:
        subprocess.run(['ar', 'p', str(library), 'db_impl_compaction_flush.o'], stdout=out, check=True)
    symbols = subprocess.check_output(['nm', '-C', str(original_obj)], text=True)
    assert 'SyncPoint::' not in symbols, 'unexpected original debug configuration'
    offsets = {}
    for name, obj in [('original', original_obj), ('new', objects / 'db_impl_compaction_flush.o')]:
        data = subprocess.check_output(['objdump', '-drC', str(obj)], text=True)
        block = re.search(r'<rocksdb::DBImpl::ShouldPurge\(unsigned long\) const>:\n(.*?)(?:\n\n|$)', data, re.S)
        assert block, 'missing ShouldPurge ABI observation'
        (target / ('abi-' + name + '.log')).write_text(block.group(0))
        offsets[name] = sorted({int(x, 16) for x in re.findall(r'(0x[0-9a-f]+)\(%(?:rdi|r8)\)', block.group(1))
                                if int(x, 16) >= 0x1000})
    assert offsets['original'] and offsets['original'] == offsets['new'], offsets
    (target / 'core_abi_check.json').write_text(json.dumps(dict(
        passed=True, NDEBUG=True, ShouldPurge_member_offsets=offsets,
        scope='one known failing member-access function, plus subsequent POSIX functional check; not a complete ABI proof'), indent=2) + '\n')
    run([*command, '-c', str(target / 'probe.cc'), '-o', str(objects / 'probe.o')], 'compile-probe')
    run(['g++', str(objects / 'probe.o'), *built, str(library),
         '-Wl,--strip-debug', '-Wl,-Map,' + str(target / 'link.map'),
         '-lzbd', '-lpthread', '-lrt', '-ldl', '-lsnappy', '-lgflags', '-lz', '-lbz2',
         '-llz4', '-lzstd', '-u', 'zenfs_filesystem_reg', '-o', str(target / 'caza_probe')], 'link')
    mapping = (target / 'link.map').read_text().split('Discarded input sections', 1)[0]
    assert all('librocksdb_debug.a(' + Path(unit).stem + '.o)' not in mapping for unit in units)
    assert digest(library) == before
    for name, expected in identity['source_sha256'].items():
        assert digest(src / name) == expected, 'build changed ' + name
    run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
         str(target / 'fault_io.cc'), '-ldl', '-o', str(target / 'fault_io.so')], 'compile-fault-library')
    run([sys.executable, str(target / 'fault_scope_test.py'), str(target)], 'fault-scope')
    (target / 'build_complete.json').write_text(json.dumps(dict(
        passed=True, affected_units=units, original_library_unchanged=True,
        original_library_sha256=before, binary_sha256=digest(target / 'caza_probe'),
        NDEBUG=True, known_failing_member_offsets_match=True,
        supported_cases=['basic', 'direct', 'rr', 'pressure', 'gc', 'fault', 'nospace'],
        probe_sha256=digest(target / 'probe.cc'), rr_probe_sha256=digest(target / 'rr_probe.h'),
        pressure_probe_sha256=digest(target / 'pressure_probe.h'),
        nospace_probe_sha256=digest(target / 'nospace_probe.h'),
        fault_source_sha256=digest(target / 'fault_io.cc'), fault_library_sha256=digest(target / 'fault_io.so'),
        implementation_complete=False, device_verified=False), indent=2) + '\n')
    env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', ZENFS_RELATED_WORK='caza')
    run([str(target / 'caza_probe'), 'posix', str(target)], 'posix-smoke', env)
    run([str(target / 'caza_probe'), 'posix', str(target)], 'posix-direct-smoke',
        dict(env, CAZA_TEST_CASE='direct'))
    run([str(target / 'caza_probe'), 'posix', str(target)], 'posix-pressure-smoke',
        dict(env, CAZA_TEST_CASE='pressure'))
    (target / 'posix_complete.json').write_text(json.dumps(dict(
        passed=True, keys=2048, rounds=2, latest_values_verified=True,
        checksums_verified=True, reopened=True, device_verified=False), indent=2) + '\n')


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
