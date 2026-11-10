"""Extract diagnostic chronology from an aborted run without claiming trace closure."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import struct


def analyze(folder):
    native = folder / 'run.zenfs.log'
    lines = native.read_text().splitlines()
    candidates = []; frames = []; rounds = []; errors = []
    for i, line in enumerate(lines, 1):
        row = re.search(r'OAZA-GC candidate: zone=(\d+) valid=(\d+) capacity=(\d+) label=(\d+) cost=([\d.]+)', line)
        if row:
            zone, valid, capacity, label, cost = row.groups()
            candidates.append(dict(zone=int(zone), valid=int(valid), capacity=int(capacity),
                                   label=int(label), printed_cost=cost, line=i))
        row = re.search(r'OAZA-GC frame: invalid=(\d+) allocated=(\d+) zones=(\d+) requested=(\d+) selected=(\d+)', line)
        if row:
            invalid, allocated, zones, requested, selected = map(int, row.groups())
            assert selected == min(requested, len(candidates))
            ranked = sorted(candidates, key=lambda z: (Decimal(z['printed_cost']), z['zone']))
            chosen = ranked[:selected]
            frames.append(dict(line=i, text=line, invalid=invalid, allocated=allocated,
                               requested=requested, selected=selected, candidates=candidates,
                               inferred_from_printed_costs=chosen,
                               inferred_selected_live_bytes=sum(z['valid'] for z in chosen)))
            candidates = []
        row = re.search(r'ZC 统计：轮次 (\d+)，本轮迁移 (\d+) 字节，本轮重置 (\d+) 个 Zone', line)
        if row:
            n, copied, resets = map(int, row.groups())
            rounds.append(dict(round=n, copied_bytes=copied, resets=resets, line=i, text=line))
        if 'ZC 迁移失败：' in line or '迁移预留保留尾部：' in line:
            errors.append(dict(line=i, text=line))
    trace = folder / 'run-composition.bin'
    header = trace.read_bytes()[:32]
    assert len(header) == 32 and header[:8] == b'RRZCCP01'
    events, dropped, complete = struct.unpack('<3Q', header[8:])
    assert complete == 0, 'this diagnostic is for incomplete traces only'
    run_lines = (folder / 'run.log').read_text().splitlines()
    zero_frames = [f for f in frames if not f['selected']]
    return dict(diagnostic_only=True, full_workload_verified=False,
        frames=frames, gc_rounds=rounds, allocation_errors=errors,
        no_candidate_frame_count=len(zero_frames),
        first_no_candidate_frame=zero_frames[0]['text'] if zero_frames else None,
        business_errors=[dict(line=i, text=line) for i, line in enumerate(run_lines, 1)
                         if 'what():' in line or 'Received signal' in line],
        composition_trace=dict(bytes=trace.stat().st_size, declared_events=events,
                               dropped=dropped, complete=bool(complete)),
        source_hashes={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (native, folder / 'run.log', trace, Path(__file__))},
        boundaries=['candidate order inferred from rounded printed costs, not a fresh selection execution',
                    'per-candidate capacity admission does not reserve space for the entire batch',
                    'file-name-ordered migration and competing foreground allocation need separate validation',
                    'no complete copy/mapping/reset ledger or post-failure NAND snapshot is available',
                    'observed literal cost preference is not proof that changing the cost alone prevents exhaustion'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.folder)
    with args.output.open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
