"""Compress closed legacy text logs only after exact immutable-tar checks."""
import argparse
import json
from pathlib import Path
import tarfile

from compact_closed_requests import closed_no_guest, stream_hash
from run_comparison_host import IMAGE_DIR, closed_archive, atomic_json, digest
from result_storage import maintenance_guard
from compress_archived_ftl_logs import compress


def main(oaza_admission=False):
    receipt = IMAGE_DIR / ('OAZA字面版-旧文本无损压缩-20261110.json' if oaza_admission
                           else 'ColdMap续跑-旧文本无损压缩-20261110.json')
    patterns = ['*202610*-results', '*2026110[1-5]-results'] if oaza_admission else ['*202610*-results']
    minimum = 1_000_000 if oaza_admission else 5_000_000
    assert not receipt.exists()
    with maintenance_guard(IMAGE_DIR):
        closed_no_guest()
        plans = []
        for root in sorted({root for pattern in patterns for root in IMAGE_DIR.glob(pattern)}):
            try:
                state = json.loads((root / '状态.json').read_text())
                stage = json.loads((root / '阶段归档.json').read_text())
                state['镜像处理']['archive']
            except (KeyError, FileNotFoundError):
                continue
            if not (stage['完整'] and stage['业务成功'] and not stage['客机仍存活']
                    and state['当前客机PID'] is None): continue
            files = [p for p in root.rglob('*.log') if p.stat().st_size >= minimum
                     and p.stat().st_nlink == 1 and not p.is_symlink()
                     and not p.with_suffix('.log.gz').exists()
                     and (p.is_relative_to(root / state['标签']) or p.is_relative_to(root / '逐臂采证'))]
            if not files: continue
            state, archive = closed_archive(root)
            wanted = {}
            for path in files:
                relative = path.relative_to(root)
                if relative.parts[0] == '逐臂采证':
                    relative = Path(state['标签']).joinpath(*relative.parts[1:])
                else: assert relative.parts[0] == state['标签']
                sha = digest(path)
                wanted.setdefault(str(relative), []).append((str(path), sha))
            checked = set()
            with tarfile.open(archive, 'r|gz') as tar:
                for member in tar:
                    if member.name not in wanted: continue
                    assert member.isfile() and member.name not in checked
                    with tar.extractfile(member) as stream: sha = stream_hash(stream)
                    assert all(value == sha for _, value in wanted[member.name])
                    checked.add(member.name)
            assert checked == set(wanted)
            plans.append(dict(root=str(root), archive=str(archive),
                archive_sha256=state['镜像处理']['archive_sha256'], files=wanted))
        result = dict(status='prepared', plans=plans, compressed=[],
                      boundary='immutable archives unchanged; gzip restoration receipts retain exact original bytes')
        atomic_json(receipt, result)
        for plan in plans:
            closed_no_guest(); closed_archive(Path(plan['root']))
            for paths in plan['files'].values():
                for path, sha in paths:
                    assert digest(Path(path)) == sha
                    row = compress(Path(path), apply=True)
                    result['compressed'].append(row); atomic_json(receipt, result)
            assert digest(Path(plan['archive'])) == plan['archive_sha256']
        result['status'] = 'complete'
        result['reclaimed_bytes'] = sum(r['释放字节'] for r in result['compressed'])
        atomic_json(receipt, result)
        print(json.dumps(dict(status=result['status'], files=len(result['compressed']),
                              reclaimed_bytes=result['reclaimed_bytes'])), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--oaza-admission', action='store_true')
    main(parser.parse_args().oaza_admission)
