"""Read-only immutable archive, configuration, source and process verification."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

from run_comparison_host import admit_build, ARMS, METHODS, closed_archive, digest
from comparison_source import BASE
from analyze_comparison import analyze_arm, compare
from cache_config_guard import check as cache_check


def read(path): return json.loads(path.read_text())


def verify(root, output):
    assert not output.exists(), 'refuse overwrite'
    state = read(root / 'comparison-state.json'); plan = read(root / 'comparison-plan.json')
    assert state['stage'] == 'complete' and state['completed'] == [arm for arm, _, _ in ARMS]
    assert plan['arms'] == [list(x) for x in ARMS]
    reports = {}; folders = {}; archives = {}
    for arm, method, _ in ARMS:
        parent = root / arm; saved, archive = closed_archive(parent)
        assert read(parent / '阶段归档.json')['业务成功']
        lease = read(parent / '镜像生命周期.json')
        assert lease['status'] == 'deleted' and not Path(lease['image']).exists()
        assert not (parent / 'payload.tar.gz').exists()
        folder = parent / saved['标签']; folders[arm] = folder
        verified = {}
        with tarfile.open(archive, 'r|gz') as tar:
            for member in tar:
                prefix = saved['标签'] + '/'
                if not member.name.startswith(prefix) or not member.isfile(): continue
                name = member.name[len(prefix):]
                assert name and not Path(name).is_absolute() and '..' not in Path(name).parts
                local = folder / name
                assert local.is_file() and not local.is_symlink() and name not in verified
                h = hashlib.sha256()
                with tar.extractfile(member) as f:
                    for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
                assert h.hexdigest() == digest(local), local
                verified[name] = h.hexdigest()
        report = analyze_arm(folder, plan['windows'])
        assert json.loads(json.dumps(report)) == read(parent / 'comparison-audit.json'), 'saved per-arm audit changed'
        safety = {}
        for phase in ('load', 'run', 'verify', 'reopen'):
            snapshot = read(folder / (phase + '-end.json'))
            assert snapshot['strict']['历史最高RC'] <= 1024
            assert all(b['RC'] <= 1024 for b in snapshot['blocks'])
            assert report['trace_receipts'][phase]['原生映射核验次数'] > 0
            safety[phase] = dict(historical_highest_rc=snapshot['strict']['历史最高RC'],
                                 maximum_sampled_block_rc=max(b['RC'] for b in snapshot['blocks']),
                                 device_threshold_events=snapshot['strict']['阈值触发事件'])
        report['strict_rr_boundary_checks'] = safety
        report['cache_configuration'] = {p: cache_check((folder / (p + '.rocksdb.log')).read_text(), 1)
                                         for p in ('load', 'run', 'verify', 'reopen')}
        with (folder / 'load-layout-0.sst.csv').open() as stream: files = list(csv.DictReader(stream))
        with (folder / 'load-layout-0.extent.csv').open() as stream:
            extents = [r for r in csv.DictReader(stream) if r['文件'].endswith('.sst')]
        report['load_layout'] = dict(ssts=len(files), levels=dict(Counter(f['层级'] for f in files)),
            data_zones=len({r['Zone'] for r in extents}), extents=len(extents),
            sst_bytes=sum(int(f['字节']) for f in files), extent_bytes=sum(int(r['字节']) for r in extents),
            boundary='recorded policy-dependent layout; CSV capture is not a cross-layer atomic checkpoint')
        reports[arm] = report
        if arm != 'g2':
            proof = compare(folders['g2'], folder, reports['g2'], report)
            assert proof == read(parent / 'cross-arm-audit.json')
        archives[arm] = dict(path=str(archive), sha256=digest(archive), files=verified,
                             guest_exited=True, image_reclaimed=True)
    builds = {method: admit_build(Path(plan['build']), method)[1] for method in METHODS}
    sources = {}
    for method, tree in METHODS.items():
        base = BASE / 'workspaces' / tree
        identity = read(base / 'overlay_identity.json')
        assert all(digest(base / 'src/rocksdb' / n) == sha for n, sha in identity['source_sha256'].items())
        sources[method] = dict(files_unchanged=len(identity['source_sha256']),
                               identity_sha256=digest(base / 'overlay_identity.json'))
    repo = BASE.parents[2]
    phase4 = repo / 'research/rrzc/results/placement-ablation-20261105/阶段四'
    four = read(phase4 / '交付文件SHA256.json')
    assert all(digest(phase4 / n) == sha for n, sha in four.items())
    five = read(BASE / 'workspaces/caza-v6-20261107/本轮设备核验.json')['stage_five_current_hashes']
    assert all(digest(repo / n) == sha for n, sha in five.items())
    live = []
    for line in subprocess.check_output(['ps', '-eo', 'pid=,stat=,rss=,comm=,args='], text=True).splitlines():
        values = line.split(None, 4)
        if len(values) < 5: continue
        pid, state, rss, command, args = values
        if int(pid) == os.getpid() or state.startswith('Z'): continue
        if ((command.startswith('qemu-system') and 'joint-related-compare-' in args) or
                (command.startswith('python') and 'run_comparison_host.py' in args)):
            live.append(dict(pid=int(pid), rss_KiB=int(rss), command=args))
    assert not live, live
    result = dict(passed=True, checked_utc=datetime.now(timezone.utc).isoformat(), plan=plan,
        reports=reports, archives=archives, builds=builds, frozen_sources=sources, live_comparison_processes=live,
        stage_four_unchanged_files=len(four), stage_five_unchanged_files=len(five),
        verification_code_sha256={p.name: digest(p) for p in
            (Path(__file__), Path(__file__).with_name('analyze_comparison.py'),
             repo / 'research/rrzc/analyze_end_to_end.py', repo / 'research/rrzc/analyze_placement_integration.py')},
        full_workload_verified=plan['windows'] == 12, statistical_performance_claim=False,
        boundary='same fresh empty-device semantics and logical input; post-load layout differs by policy; CRC32C is not a bytewise value proof')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(output)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); verify(a.root.resolve(), a.output.resolve())
