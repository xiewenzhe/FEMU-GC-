"""Read-only export of the current RocksDB/ZenFS source and build identity."""
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tarfile


def source_names(cached, untracked):
    """Keep tracked source/build files and only source-like untracked additions."""
    names = {x.decode() for x in cached.split(b'\0') if x}
    suffixes = {'.c', '.cc', '.cpp', '.h', '.hh', '.hpp', '.inc', '.py', '.sh',
                '.mk', '.cmake', '.in', '.S'}
    for raw in untracked.split(b'\0'):
        if raw:
            name = raw.decode()
            if Path(name).suffix in suffixes or Path(name).name in ('Makefile', 'CMakeLists.txt'):
                names.add(name)
    return sorted(names)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def export(output):
    root = Path('/home/femu/rocksdb')
    output = Path(output).resolve()
    assert not (output / 'source.tar.gz').exists()
    library = root / 'librocksdb_debug.a'
    before = digest(library)
    assert before == 'fe0123f63993b7a7a823bd114729bdf281696eb45e3d619034396bda8316c37b'
    files = {}
    repositories = {}
    for repo in (root, root / 'plugin/zenfs'):
        def git(*args):
            return subprocess.check_output(['git', '-C', str(repo), *args])
        repositories[str(repo.relative_to(root))] = dict(
            head=git('rev-parse', 'HEAD').decode().strip(),
            status=git('status', '--porcelain', '--untracked-files=normal').decode())
        for name in source_names(git('ls-files', '--cached', '-z'),
                                 git('ls-files', '--others', '--exclude-standard', '-z')):
            relative = Path(name)
            assert not relative.is_absolute() and '..' not in relative.parts
            path = repo / relative
            if path.is_dir():  # Nested ZenFS Git tree is enumerated separately.
                continue
            assert stat.S_ISREG(path.lstat().st_mode), 'source link or missing file: ' + str(path)
            assert path.stat().st_size < 16 << 20, 'unexpected source artifact: ' + str(path)
            files[str(path.relative_to(root))] = digest(path)
    assert all(name in files for name in (
        'include/rocksdb/version.h', 'include/rocksdb/file_system.h',
        'table/block_based/block_based_table_builder.cc', 'file/writable_file_writer.cc',
        'db/builder.cc', 'db/compaction/compaction_job.cc', 'plugin/zenfs/fs/io_zenfs.cc'))
    report = dict(root=str(root), repositories=repositories, files=files,
                  original_library_sha256=before, source_only=True, nand_snapshot=False)
    identity = output / 'source_identity.json'
    with identity.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    with tarfile.open(output / 'source.tar.gz', 'x:gz') as archive:
        for name, expected in sorted(files.items()):
            path = root / name
            assert digest(path) == expected, 'source changed before export: ' + name
            archive.add(path, arcname='rocksdb/' + name, recursive=False)
        archive.add(identity, arcname='source_identity.json', recursive=False)
    assert all(digest(root / name) == expected for name, expected in files.items())
    assert digest(library) == before, 'original library changed'
    with (output / 'export_complete.json').open('x') as stream:
        json.dump(dict(passed=True, source_tar_sha256=digest(output / 'source.tar.gz'),
                       identity_sha256=digest(identity), source_file_count=len(files),
                       source_bytes=sum((root / n).stat().st_size for n in files),
                       original_library_unchanged=True), stream, indent=2)
    print('Read-only source export completed', len(files), flush=True)


if __name__ == '__main__':
    export(Path(sys.argv[1]))
