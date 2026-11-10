"""Losslessly compress analyzed failed comparison bins; immutable tar stays intact."""
import argparse
import gzip
import json
import os
from pathlib import Path
import shutil

from comparison_failure import failure_receipt
from run_comparison_host import IMAGE_DIR, digest, atomic_json
from compact_closed_requests import closed_no_guest, stream_hash
from result_storage import maintenance_guard


def plan_one(parent):
    state = json.loads((parent / '状态.json').read_text()); folder = parent / state['标签']
    wanted = json.loads((folder / 'comparison_plan.json').read_text())
    proof = failure_receipt(parent, wanted)
    assert not (parent / '失败二进制无损压缩.json').exists(), 'refuse repeat compaction'
    files = []
    for name, sha in proof['files'].items():
        path = folder / name
        if not name.endswith('.bin') or path.stat().st_size < 32 << 20: continue
        st = path.stat()
        assert st.st_nlink == 1 and not path.is_symlink()
        assert not path.with_suffix('.bin.gz').exists() and not path.with_suffix('.bin.gz.partial').exists()
        files.append(dict(name=name, path=str(path), sha256=sha, inode=st.st_ino,
                          allocated_bytes=st.st_blocks * 512, mode=st.st_mode & 0o777,
                          uid=st.st_uid, gid=st.st_gid, atime_ns=st.st_atime_ns, mtime_ns=st.st_mtime_ns))
    return dict(parent=str(parent), wanted=wanted, archive=proof['archive'],
                archive_sha256=proof['archive_sha256'], files=files)


def execute(plan):
    parent = Path(plan['parent']); failure_receipt(parent, plan['wanted']); closed_no_guest()
    assert digest(Path(plan['archive'])) == plan['archive_sha256']
    receipt = dict(stage='prepared', archive=plan['archive'], archive_sha256=plan['archive_sha256'], files=[],
        full_workload_verified=False,
        restore='gzip-decompress a .bin.gz to a NEW temporary file, verify sha256, then use it for analysis; exact original member also stays in immutable tar')
    marker = parent / '失败二进制无损压缩.json'; assert not marker.exists(); atomic_json(marker, receipt)
    for item in plan['files']:
        path = Path(item['path']); assert path.stat().st_ino == item['inode'] and digest(path) == item['sha256']
        packed = path.with_suffix('.bin.gz'); partial = path.with_suffix('.bin.gz.partial')
        with path.open('rb') as inp, partial.open('xb') as out:
            with gzip.GzipFile(filename='', mode='wb', compresslevel=1, fileobj=out, mtime=0) as gz:
                shutil.copyfileobj(inp, gz, 1 << 20)
            out.flush(); os.fsync(out.fileno())
        with gzip.open(partial, 'rb') as f: assert stream_hash(f) == item['sha256']
        assert partial.stat().st_uid == item['uid'] and partial.stat().st_gid == item['gid']
        partial.chmod(item['mode']); os.utime(partial, ns=(item['atime_ns'], item['mtime_ns'])); partial.rename(packed)
        receipt['files'].append(dict(name=item['name'], sha256=item['sha256'],
            compressed_sha256=digest(packed), reclaimed_bytes=item['allocated_bytes'] - packed.stat().st_blocks * 512))
        atomic_json(marker, receipt)
        assert path.stat().st_ino == item['inode'] and digest(path) == item['sha256']; path.unlink()
    assert digest(Path(plan['archive'])) == plan['archive_sha256']
    failure_receipt(parent, plan['wanted'])
    receipt['stage'] = 'complete'; atomic_json(marker, receipt)
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--execute', action='store_true'); args = p.parse_args(); assert not args.output.exists()
    roots = [IMAGE_DIR / 'joint-related-compare-full-v2-20261109-results' / name
             for name in ('oaza-literal', 'oaza-adapted', 'cold-printed', 'cold-survival')]
    roots += [IMAGE_DIR / 'joint-related-compare-bootstrap-oaza-v1-20261109-results/oaza-adapted']
    with maintenance_guard(IMAGE_DIR):
        closed_no_guest(); plans = [plan_one(root) for root in roots]
        result = dict(executed=args.execute, plans=plans)
        if args.execute:
            result['receipts'] = [execute(plan) for plan in plans]
            result['reclaimed_bytes'] = sum(row['reclaimed_bytes'] for r in result['receipts'] for row in r['files'])
        atomic_json(args.output, result)
        print(result.get('reclaimed_bytes', sum(f['allocated_bytes'] for plan in plans for f in plan['files'])))
