"""Losslessly compress closed legacy request copies; immutable archives stay intact."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

from run_comparison_host import IMAGE_DIR, closed_archive, digest, atomic_json
from result_storage import maintenance_guard

NAMES = ('joint-controlled-pool-tail-stream-v4-20261104-results',
         'joint-controlled-first-action-v1-20261105-results',
         'joint-controlled-target-compare-v1-20261103-results',
         'joint-controlled-pair-admission-v1-20261103-results',
         'joint-controlled-pool-tail-v3-20261104-results')


def stream_hash(stream):
    h = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1 << 20), b''): h.update(chunk)
    return h.hexdigest()


def closed_no_guest():
    qemu = subprocess.run(['ps', '-C', 'qemu-system-x86_64', '-o', 'args='], text=True, capture_output=True)
    assert str(IMAGE_DIR) + '/' not in qemu.stdout, 'a project guest is active'


def plan_one(root):
    state, archive = closed_archive(root)
    assert json.loads((root / '阶段归档.json').read_text())['业务成功']
    groups = {}
    for p in root.rglob('请求提交.bin'):
        assert p.is_file() and not p.is_symlink()
        st = p.stat(); groups.setdefault((st.st_dev, st.st_ino), []).append(p)
    proof = {}; requested = {}
    for paths in groups.values():
        st = paths[0].stat()
        assert st.st_nlink == len(paths), 'untracked hard-link alias'
        assert all((p.stat().st_mode, p.stat().st_uid, p.stat().st_gid) ==
                   (st.st_mode, st.st_uid, st.st_gid) for p in paths)
        assert all(not p.with_suffix('.bin.gz').exists() and
                   not p.with_suffix('.bin.gz.partial').exists() for p in paths)
        originals = [p for p in paths if p.is_relative_to(root / state['标签'])]
        assert len(originals) <= 1, 'multiple archived originals share an unexpected inode'
        if originals:
            original = originals[0]; archive_member = str(original.relative_to(root))
        else:
            assert len(paths) == 1 and paths[0].is_relative_to(root / '逐臂采证'), 'unknown request-copy location'
            original = paths[0]
            relative = original.relative_to(root / '逐臂采证')
            retained = root / state['标签'] / relative
            archive_member = str(retained.relative_to(root))
            if retained.exists():
                assert retained.is_file() and not retained.is_symlink(), 'unsafe retained original'
                assert digest(retained) == digest(original), 'collected copy differs from its original'
            # Older compaction kept only the collected local copy. The exact
            # label/relative member must still exist in the immutable tar and
            # its streamed hash must match below; absence is never inferred.
        sha = digest(original)
        assert all(digest(p) == sha for p in paths)
        key = str(original.relative_to(root))
        assert archive_member not in requested or requested[archive_member] == sha
        requested[archive_member] = sha
        proof[key] = dict(paths=[str(p) for p in paths], archive_member=archive_member,
                          sha256=sha, original_bytes=st.st_size,
                          allocated_bytes=st.st_blocks * 512, inode=st.st_ino, mode=st.st_mode & 0o777,
                          uid=st.st_uid, gid=st.st_gid, mtime_ns=st.st_mtime_ns, atime_ns=st.st_atime_ns)
    verified = set()
    with tarfile.open(archive, 'r|gz') as tar:
        for member in tar:
            if member.name not in requested: continue
            assert member.isfile() and member.name not in verified
            with tar.extractfile(member) as source: assert stream_hash(source) == requested[member.name]
            verified.add(member.name)
    assert verified == set(requested)
    return dict(root=str(root), archive=str(archive), archive_sha256=digest(archive),
                groups=proof, original_allocated_bytes=sum(v['allocated_bytes'] for v in proof.values()))


def execute_one(plan):
    root = Path(plan['root'])
    assert digest(Path(plan['archive'])) == plan['archive_sha256']
    closed_archive(root); closed_no_guest()
    record = root / '共同负载准入-旧请求无损压缩-20261109.json'
    assert not record.exists()
    receipt = dict(status='prepared', plan=plan, compressed=[],
        restore='for every paths entry: gzip-decompress its .bin.gz into a NEW temporary file; verify sha256; rename to original path; immutable tar also retains the exact original member',
        boundary='only already analyzed, closed legacy request copies; archives, metadata, inputs and program identities unchanged')
    atomic_json(record, receipt)
    for member, group in plan['groups'].items():
        paths = [Path(p) for p in group['paths']]
        source = root / member; before = source.stat()
        assert before.st_ino == group['inode'] and before.st_nlink == len(paths)
        compressed = source.with_suffix('.bin.gz'); partial = source.with_suffix('.bin.gz.partial')
        with source.open('rb') as inp, partial.open('xb') as raw:
            with gzip.GzipFile(filename='', mode='wb', compresslevel=1, fileobj=raw, mtime=0) as out:
                shutil.copyfileobj(inp, out, 1 << 20)
            raw.flush(); os.fsync(raw.fileno())
        with gzip.open(partial, 'rb') as inp: assert stream_hash(inp) == group['sha256']
        assert digest(source) == group['sha256'] and source.stat().st_ino == group['inode']
        assert partial.stat().st_uid == group['uid'] and partial.stat().st_gid == group['gid']
        partial.chmod(group['mode']); os.utime(partial, ns=(group['atime_ns'], group['mtime_ns']))
        partial.rename(compressed)
        for alias in paths:
            if alias != source: os.link(compressed, alias.with_suffix('.bin.gz'))
        row = dict(original_member=group['archive_member'], local_source=member,
                   sha256=group['sha256'], compressed_sha256=digest(compressed),
                   original_bytes=group['original_bytes'], compressed_bytes=compressed.stat().st_size,
                   paths=group['paths'],
                   reclaimed_bytes=group['allocated_bytes'] - compressed.stat().st_blocks * 512)
        receipt['compressed'].append(row); atomic_json(record, receipt)
        # Every alias is known, immutable tar verified, compressed bytes verified,
        # and restoration receipt synced before the redundant raw copies go away.
        for alias in paths:
            assert alias.stat().st_ino == group['inode'] and digest(alias) == group['sha256']
            alias.unlink()
    assert digest(Path(plan['archive'])) == plan['archive_sha256']
    receipt['status'] = 'complete'
    receipt['reclaimed_bytes'] = sum(r['reclaimed_bytes'] for r in receipt['compressed'])
    atomic_json(record, receipt)
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--execute', action='store_true')
    args = p.parse_args(); assert not args.output.exists()
    with maintenance_guard(IMAGE_DIR):
        closed_no_guest()
        plans = [plan_one(IMAGE_DIR / name) for name in NAMES]
        result = dict(executed=args.execute, plans=plans,
                      original_allocated_bytes=sum(p['original_allocated_bytes'] for p in plans))
        if args.execute:
            result['receipts'] = [execute_one(p) for p in plans]
            result['reclaimed_bytes'] = sum(r['reclaimed_bytes'] for r in result['receipts'])
        atomic_json(args.output, result)
        print(json.dumps({k: v for k, v in result.items() if k not in ('plans', 'receipts')}, ensure_ascii=False))


if __name__ == '__main__': main()
