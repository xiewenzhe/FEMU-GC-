"""Diagnose the stopped v9 literal arm from archived native logs, without a full ledger claim."""
from decimal import Decimal
import json
from pathlib import Path
import re

from run_oaza_literal_full_host import LABEL, PREVIOUS, OVERLAY, admit_reused_build
from run_comparison_host import IMAGE_DIR, atomic_json, digest, failure_receipt
from comparison_source import BASE, METHODS
from analyze_comparison_failure import analyze


def rounds_from_native(lines, diagnostic):
    rounds = []
    for index, frame in enumerate(diagnostic['frames']):
        start = frame['line'] - 1
        stop = diagnostic['frames'][index + 1]['line'] - 1 if index + 1 < len(diagnostic['frames']) else len(lines)
        batches = []; resets = []; summary = None
        for i in range(start, stop):
            line = lines[i]
            batch = re.search(r'OAZA-GC source batch: zone=(\d+) files=(\d+) selected_set_unchanged=1', line)
            if batch: batches.append(dict(zone=int(batch[1]), files=int(batch[2]), line=i + 1))
            reset = re.search(r'Zone 重置诊断：Zone=(\d+)，状态=成功，主机代际=(\d+)/(\d+)', line)
            if reset:
                zone, before, after = map(int, reset.groups())
                assert after == before + 1
                resets.append(dict(zone=zone, previous_generation=before, generation=after, line=i + 1))
            match = re.search(r'ZC 统计：轮次 (\d+)，本轮迁移 (\d+) 字节，本轮重置 (\d+) 个 Zone', line)
            if match:
                assert summary is None
                summary = dict(round=int(match[1]), copied_bytes=int(match[2]),
                               source_empty_count_at_return=int(match[3]), line=i + 1)
                break  # Later RR batches belong to another maintenance call.
        chosen = frame['inferred_from_printed_costs']
        chosen_set = {row['zone'] for row in chosen}
        actual = [row['zone'] for row in batches]
        assert actual == sorted(set(actual)) and set(actual) <= chosen_set, (index, actual, chosen_set)
        successful_source_resets = [r for r in resets if r['zone'] in chosen_set]
        capacity = sum(row['capacity'] for row in chosen)
        live = sum(row['valid'] for row in chosen)
        if summary:
            assert set(actual) == chosen_set
            assert summary['copied_bytes'] == live
        unselected = sorted((row for row in frame['candidates'] if row['zone'] not in chosen_set),
                            key=lambda row: (row['valid'], row['zone']))[:3]
        rounds.append(dict(frame_line=frame['line'], completed=summary is not None, summary=summary,
            chosen=chosen, source_batches=batches, selected_valid_bytes=live,
            selected_capacity_bytes=capacity, selected_invalid_bytes=capacity - live,
            selected_valid_fraction=str(Decimal(live) / capacity),
            successful_source_reset_events=successful_source_resets,
            smallest_valid_unselected=unselected))
    return rounds


def main():
    root = IMAGE_DIR / (LABEL + '-results'); parent = root / 'oaza-literal'
    failure = failure_receipt(parent, dict(arm='oaza-literal', method='oaza_gc', variant='literal', windows=12))
    assert failure == json.loads((root / 'literal-failure-audit.json').read_text())
    folder = parent / json.loads((parent / '状态.json').read_text())['标签']
    _, build = admit_reused_build(PREVIOUS / 'build')
    assert failure['binary_sha256'] == build['binary_sha256']
    diagnostic = analyze(folder)
    diagnostic['boundaries'] = [text for text in diagnostic['boundaries'] if not text.startswith('file-name-ordered')]
    native = (folder / 'run.zenfs.log').read_text().splitlines()
    rounds = rounds_from_native(native, diagnostic)
    completed = [row for row in rounds if row['completed']]
    repo = BASE.parents[2]
    protected = {}
    for tree in [*METHODS.values(), 'oaza-gc-v9-20261109', 'coldmap-v6-20261109']:
        source = BASE / 'workspaces' / tree
        identity = json.loads((source / 'overlay_identity.json').read_text())
        assert all(digest(source / 'src/rocksdb' / n) == sha for n, sha in identity['source_sha256'].items())
        protected[tree] = len(identity['source_sha256'])
    four = repo / 'research/rrzc/results/placement-ablation-20261105/阶段四'
    four_hashes = json.loads((four / '交付文件SHA256.json').read_text())
    assert all(digest(four / n) == sha for n, sha in four_hashes.items())
    five = json.loads((BASE / 'workspaces/caza-v6-20261107/本轮设备核验.json').read_text())['stage_five_current_hashes']
    assert all(digest(repo / n) == sha for n, sha in five.items())
    result = dict(diagnostic_only=True, full_workload_verified=False, all_methods_complete=False,
        failure=failure, native_diagnostic=diagnostic, rounds=rounds,
        completed_rounds_copied_bytes=sum(row['summary']['copied_bytes'] for row in completed),
        complete_reset_events_in_completed_rounds=sum(len(row['successful_source_reset_events']) for row in completed),
        reset_statistic_semantics='MigrateExtents counts selected source Zones still empty at return; not Reset command count',
        reset_statistic_source=str(OVERLAY / 'src/rocksdb/plugin/zenfs/fs/fs_zenfs.cc'),
        reset_statistic_source_sha256=digest(OVERLAY / 'src/rocksdb/plugin/zenfs/fs/fs_zenfs.cc'),
        frozen_source_files=protected, stage_four_unchanged_files=len(four_hashes), stage_five_unchanged_files=len(five),
        caveats=['run composition trace is only an incomplete header after abort',
            'native round sums exclude unfinished final migration; no full copy/mapping/reset ledger',
            'candidate invalid bytes are an opportunity bound, not measured net free space',
            'selection preference alone is not proof that changing the formula prevents the failure'])
    output = BASE / 'workspaces/comparison-20261109-v1/OAZA字面版v9完整复验失败核验.json'
    assert not output.exists(); atomic_json(output, result); print(output, flush=True)


if __name__ == '__main__': main()
