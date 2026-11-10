"""Verify immutable related-method archives and original-result protection; no guest launch."""
import argparse
import csv
from datetime import datetime
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
import tarfile
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[2]
sys.path[:0] = [str(HERE), str(BASE)]
from export_source_host import digest
from result_storage import closed_archive
from run_device_host import audit, mapping_transition


def read(path): return json.loads(path.read_text())


def phase_summary(folder, mode, report):
    log = (folder / ('zenfs-' + mode + '.log')).read_text()
    zc = [(int(n), int(b), int(r)) for n, b, r in re.findall(
        r'ZC 统计：轮次 (\d+)，本轮迁移 (\d+) 字节，本轮重置 (\d+)', log)]
    rr = [(int(b), int(r)) for b, r in re.findall(
        r'RR 统计：尝试 \d+ 次，本次迁移 (\d+) 字节，本次重置 (\d+)', log)]
    rr_sources = {int(z) for z in re.findall(r'RR 开始：Zone (\d+)', log)}
    copied = sum(x[1] for x in zc) + sum(x[0] for x in rr)
    assert copied == report['有效复制字节'], 'native GC/RR byte totals do not match raw trace'
    candidates = []; rounds = []; expected = set()
    for line in log.splitlines():
        m = re.search(r'OAZA-GC candidate: zone=(\d+).*?cost=([0-9.]+)', line)
        if m: candidates.append((Decimal(m[2]), int(m[1])))
        m = re.search(r'OAZA-GC frame:.*?requested=(\d+) selected=(\d+)', line)
        if m:
            chosen = [z for _, z in sorted(candidates)[:int(m[1])]]
            assert len(chosen) == int(m[2])
            expected.update(chosen)
            rounds.append(dict(candidates=len(candidates), requested=int(m[1]), selected_by_logged_cost=chosen))
            candidates = []
    cold_candidates = {}; cold_rounds = []; cold_expected = set(); cold_frame = None
    for line in log.splitlines():
        m = re.search(r'ColdMap candidate: zone=(\d+) valid=(\d+) capacity=(\d+) cold=([0-9.]+) score=([0-9.]+)', line)
        if m:
            zone, valid, capacity = map(int, m.group(1, 2, 3))
            cold, score = map(Decimal, m.group(4, 5))
            assert 0 < valid < capacity and 0 <= cold <= 1
            expected_score = Decimal(capacity - valid) * cold / valid
            # Both printed operands have twelve decimal places; retain that precision boundary.
            tolerance = Decimal(capacity) / valid * Decimal('0.000000000001')
            assert abs(score - expected_score) <= tolerance
            cold_candidates[zone] = score
        m = re.search(r'ColdMap frame: snapshot=(\d+) compactions=(\d+) depth=(\d+) simulated=(\d+) candidates=(\d+)', line)
        if m:
            cold_frame = dict(zip(('snapshot', 'compactions', 'depth', 'simulated', 'candidates'), map(int, m.groups())))
            assert cold_frame['candidates'] == len(cold_candidates)
        m = re.search(r'ColdMap selected: zone=(\d+) direction=(\S+)', line)
        if m:
            zone = int(m[1]); assert zone in cold_candidates
            assert cold_candidates[zone] + Decimal('0.000000000001') >= max(cold_candidates.values())
            cold_rounds.append(dict(candidates=len(cold_candidates), selected=zone, direction=m[2],
                                    maximum_logged_score=str(max(cold_candidates.values())), frame=cold_frame))
            cold_expected.add(zone); cold_candidates = {}; cold_frame = None
    actual = {c['源Zone'] for c in report['复制明细'] if c['映射结局'] == '提交'}
    if rounds or 'OAZA-GC' in log:
        assert actual == expected | rr_sources, 'actual sources differ from cost-selected GC and native RR union'
    if cold_rounds:
        assert actual == cold_expected | rr_sources, 'actual sources differ from ColdMap-selected GC and native RR union'
    return dict(gc_rounds=zc, rr_rounds=rr, rr_sources=sorted(rr_sources),
                gc_copied_bytes=sum(x[1] for x in zc), rr_copied_bytes=sum(x[0] for x in rr),
                trace_total_matches_native=True, oaza_cost_rounds=rounds, coldmap_score_rounds=cold_rounds,
                actual_copy_sources=sorted(actual), expected_gc_sources=sorted(expected),
                boundary='per phase; GC and RR totals are distinct; source union is not a per-event timestamp proof')


def recovery_costs(folder, variant):
    """Recompute first post-mount costs from persisted keys and pre-GC native extents."""
    def rows(name):
        with (folder / name).open() as stream: return list(csv.DictReader(stream))
    files = {int(f['sst']): f for f in rows('oaza-recovered-keys.csv')}
    zones = {int(z['zone']): z for z in rows('oaza-recovered-zones.csv')}
    parts = {}
    for row in rows('device-mapping.csv'):
        zone = int(row['zone']); number = int(row['sst'])
        parts.setdefault(zone, {})[number] = parts.setdefault(zone, {}).get(number, 0) + int(row['length'])
    log = (folder / 'zenfs-verify.log').read_text().split('OAZA-GC frame:', 1)[0]
    candidates = re.findall(r'OAZA-GC candidate: zone=(\d+) valid=(\d+) capacity=(\d+) label=(\d+) cost=([0-9.]+)', log)
    assert candidates and files and variant in ('literal', 'adapted')
    proof = []
    for start, valid, capacity, label, cost in candidates:
        start, valid, capacity, label = map(int, (start, valid, capacity, label))
        z = zones[start]; assert int(z['valid']) == valid and int(z['native_label']) == 0
        assert int(z['file_label']) == label > 1 and sum(parts[start].values()) == valid
        priorities = []
        for number, size in sorted(parts[start].items()):
            f = files[number]; assert int(f['keys']) > 0
            reference = 0 if int(f['level']) == 1 else 1  # This recovery fixture has exactly two levels.
            overlap = sum(int(other['keys']) for other in files.values() if int(other['level']) == reference and
                          f['smallest'] <= other['largest'] and other['smallest'] <= f['largest'])
            ratio = Decimal(overlap) / int(f['keys']); amp = Decimal(f['amplification'])
            assert amp > 0
            priority = Decimal(1) if ratio == 0 or ratio > 2 * amp else ratio / amp
            priorities.append((size, priority))
        u = Decimal(valid) / capacity
        expected = ((1 - u) * priorities[-1][1] if variant == 'literal' else
                    u * sum(size * priority for size, priority in priorities) / valid) * label
        assert abs(expected - Decimal(cost)) < Decimal('0.00000000001')
        proof.append(dict(zone=start, recovered_label=label, expected_cost=str(expected), logged_cost=cost))
    return proof


def verify(output):
    assert not output.exists(), 'refuse overwrite'
    runs = []
    for parent in sorted(Path('/home/data/xwz/rrzc-baselines').glob('joint-related-*-results')):
        if not parent.name.startswith(('joint-related-coldmap-', 'joint-related-oaza-gc-',
                                       'joint-related-caza-build-joint-', 'joint-related-caza-device-joint-')): continue
        unstarted = parent / '未启动失败.json'
        if unstarted.exists():
            failure = read(unstarted)
            assert not (parent / '阶段归档.json').exists() and failure['guest_started'] is False
            log = Path(failure['log']); assert digest(log) == failure['log_sha256']
            runs.append(dict(label=parent.name.removesuffix('-results'), task_success=False,
                             launched=False, preparation_failure=failure, archive_present=False))
            print(parent.name, 'preparation failed before guest launch', flush=True)
            continue
        state, archive = closed_archive(parent)
        label = state['标签']; folder = parent / label
        lifecycle = read(parent / '镜像生命周期.json')
        assert lifecycle['status'] == 'deleted' and not Path(lifecycle['image']).exists()
        assert not (parent / 'payload.tar.gz').exists()
        verified = {}
        with tarfile.open(archive, 'r|gz') as tar:
            for member in tar:
                prefix = label + '/'
                if not member.name.startswith(prefix) or not member.isfile(): continue
                relative = member.name[len(prefix):]
                assert relative and '..' not in Path(relative).parts and not relative.startswith('/')
                local = folder / relative
                if not local.is_file(): continue
                assert relative not in verified
                h = hashlib.sha256()
                with tar.extractfile(member) as f:
                    for chunk in iter(lambda: f.read(1 << 20), b''): h.update(chunk)
                assert h.hexdigest() == digest(local), local
                verified[relative] = h.hexdigest()
        stage = read(parent / '阶段归档.json')
        row = dict(label=label, archive=str(archive), archive_sha256=digest(archive),
                   task_success=stage['业务成功'], original_error=stage['原始错误'],
                   image_reclaimed=True, process_exited=True, verified_files_sha256=verified)
        if (folder / 'build_complete.json').exists(): row['build'] = read(folder / 'build_complete.json')
        if (folder / 'device_complete.json').exists(): row['device'] = read(folder / 'device_complete.json')
        if (folder / 'coldmap-model.csv').exists():
            def csv_rows(name):
                with (folder / name).open() as stream: return list(csv.DictReader(stream))
            model, = csv_rows('coldmap-model.csv')
            steps = csv_rows('coldmap-model-steps.csv')
            originals = csv_rows('coldmap-input-files.csv')
            largest_original = max(int(f['number']) for f in originals)
            assert int(model['steps']) == len(steps)
            assert int(model['created']) == sum(int(s['created']) for s in steps)
            assert int(model['generated_pivots']) == sum(int(s['pivot']) > largest_original for s in steps)
            if stage['业务成功']:
                assert int(model['compactions']) == int(model['depth']) == len(steps) == 3
                assert int(model['created']) > 0 and int(model['generated_pivots']) > 0
            row['coldmap_real_snapshot_model'] = dict(summary=model, steps=steps,
                boundary='real native snapshot and simulated metadata; not actual predicted compactions or a performance claim')
        reports = []
        row['audits'] = {}
        row['native_phases'] = {}
        for mode in ('device', 'verify'):
            path = folder / (mode + '-composition.bin')
            if not path.exists(): continue
            report = audit(path); reports.append(report)
            assert report['事件账目闭合']
            saved = parent / (mode + '-composition-audit.json')
            if saved.exists(): assert read(saved) == report
            row['audits'][mode] = {k: report[k] for k in ('事件账目闭合', '原生映射核验次数',
                '忙文件快照跳过次数', '有效复制字节', '各类事件')}
            row['native_phases'][mode] = phase_summary(folder, mode, report)
        if len(reports) == 2 and (folder / 'device-mapping.csv').exists() and (folder / 'verify-mapping.csv').exists():
            transition = mapping_transition(folder, reports)
            assert transition['passed']
            row['mapping_transition'] = transition
            initial = parent / 'physical-mapping-audit.json'
            if initial.exists():
                row['initial_post_archive_mapping_audit'] = read(initial)
                row['initial_post_archive_mapping_audit_sha256'] = digest(initial)
        if stage['业务成功'] and row.get('device', {}).get('method') == 'coldmap' and row['device']['case'] == 'joint':
            rounds = row['native_phases']['device']['coldmap_score_rounds']
            assert any(r['frame'] and r['candidates'] >= 2 and r['frame']['simulated'] > 0 for r in rounds)
            assert row['native_phases']['device']['gc_copied_bytes'] > 0
            row['coldmap_joint_gc_verified'] = True
        if stage['业务成功'] and row.get('device', {}).get('method') == 'caza' and row['device']['case'] == 'joint':
            assert row['native_phases']['device']['gc_copied_bytes'] > 0
            assert row['native_phases']['device']['rr_copied_bytes'] > 0
            row['caza_gc_rr_same_run_verified'] = True
        if stage['业务成功'] and row.get('device', {}).get('case') == 'recovery':
            assert row['native_phases']['device']['gc_copied_bytes'] == 0
            assert row['native_phases']['verify']['gc_copied_bytes'] > 0
            row['oaza_recomputed_recovery_costs'] = recovery_costs(folder, row['device']['variant'])
        runs.append(row); print(label, row['task_success'], flush=True)
    caza = BASE / 'workspaces/caza-v6-20261107'
    source = read(caza / 'overlay_identity.json')['source_sha256']
    assert all(digest(caza / 'src/rocksdb' / n) == h for n, h in source.items())
    phase4 = ROOT / 'research/rrzc/results/placement-ablation-20261105/阶段四'
    four = read(phase4 / '交付文件SHA256.json')
    assert all(digest(phase4 / n) == h for n, h in four.items())
    five = read(caza / '本轮设备核验.json')['stage_five_current_hashes']
    assert all(digest(ROOT / n) == h for n, h in five.items())
    live = []
    other = []
    for line in subprocess.check_output(['ps', '-eo', 'user=,pid=,rss=,comm=,args='], text=True).splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5: continue
        user, pid, rss, comm, args = parts
        if comm.startswith('qemu-system') or (comm.startswith('python') and
                any(x in args for x in ('run_native_host.py', 'common/run_device_host.py'))):
            value = dict(user=user, pid=int(pid), rss_KiB=int(rss), command=args)
            if any(x in args for x in ('joint-related-coldmap-', 'joint-related-oaza-gc-', 'joint-related-caza-',
                                        'common/run_native_host.py', 'common/run_device_host.py')):
                live.append(value)
            else:
                value['action'] = 'retained: active other task, not owned by this related-work run'
                other.append(value)
    assert not live, live
    identities = {}
    for relative in ('workspaces/coldmap-v5-20261109', 'workspaces/oaza-gc-v7-20261109'):
        path = BASE / relative; identity = read(path / 'overlay_identity.json')
        assert all(digest(path / 'src/rocksdb' / n) == h for n, h in identity['source_sha256'].items())
        identities[relative] = dict(identity_sha256=digest(path / 'overlay_identity.json'),
                                   source_files_verified=len(identity['source_sha256']))
    result = dict(checked_beijing=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        evidence_verified=True, all_methods_complete=False, performance_experiment=False,
        runs=runs, current_sources=identities, caza_source_files_unchanged=len(source),
        stage_four_unchanged_files=len(four), stage_five_unchanged_files=len(five),
        resources=dict(live_related_work_guests_and_supervisors=live, other_active_guests=other,
            MemAvailable=next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))),
        boundary='functional evidence only; failed tasks retain their original status; no full NAND checkpoint or statistical performance claim')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(output)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); verify(a.output.resolve())
