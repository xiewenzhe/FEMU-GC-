"""Publish stage-separated metrics only after immutable six-arm verification."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def read(path): return json.loads(path.read_text())


def zone_metrics(snapshot):
    zones = snapshot['zones']['zone_list']
    assert len(zones) == 64
    used = tail = opened = active = empty = 0
    for zone in zones:
        start, wp, cap = (int(zone[k]) for k in ('slba', 'wp', 'cap'))
        assert start <= wp and cap > 0
        written = min(cap, wp - start)
        if zone['state'] == 'EMPTY':
            assert wp == start
            empty += 1
        if zone['state'] in ('IMP_OPENED', 'EXP_OPENED'): opened += 1
        if zone['state'] in ('IMP_OPENED', 'EXP_OPENED', 'CLOSED'): active += 1
        used += written * 512
        if zone['state'] not in ('FULL', 'OFFLINE', 'READONLY'): tail += (cap - written) * 512
    return dict(empty_zones=empty, open_zones=opened, active_zones=active,
                written_or_finished_bytes=used, writable_tail_bytes=tail,
                boundary='native zone report, 512-byte LBAs; written/finished space is not logical valid data')


def layout_metrics(folder, phase):
    paths = list(folder.glob(phase + '-layout-*.sst.csv'))
    if not paths: return dict(captured=False)
    path = max(paths, key=lambda p: int(p.name.removesuffix('.sst.csv').rsplit('-', 1)[1]))
    with path.open() as f: files = list(csv.DictReader(f))
    with path.with_name(path.name.removesuffix('.sst.csv') + '.extent.csv').open() as f:
        extents = list(csv.DictReader(f))
    actual = {}
    for e in extents:
        name = Path(e['文件']).name
        actual[name] = actual.get(name, 0) + int(e['字节'])
    mismatch = [f['文件'] for f in files if actual.get(Path(f['文件']).name) != int(f['字节'])]
    return dict(captured=True, source=path.name, ssts=len(files),
                metadata_sst_bytes=sum(int(f['字节']) for f in files),
                per_file_extent_sizes_equal=not mismatch, missing_or_changed_files=mismatch,
                boundary='last process layout observation; not an atomic post-close checkpoint')


def summarize(root, evidence, output):
    assert not output.exists(), 'refuse overwrite'
    proof = read(evidence); assert proof['passed']
    plan = proof['plan']; assert read(root / 'comparison-plan.json') == plan
    metrics = []; stages = []; latencies = []; remaining = []
    for arm, report in proof['reports'].items():
        parent = root / arm; state = read(parent / '状态.json'); folder = parent / state['标签']
        archive = Path(proof['archives'][arm]['path'])
        h = hashlib.sha256()
        with archive.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b''): h.update(chunk)
        assert h.hexdigest() == proof['archives'][arm]['sha256'], 'archive changed after verification'
        phase_values = {}
        for phase, data in report['phases'].items():
            clock = read(folder / (phase + '-clock.json'))
            item = dict(arm=arm, phase=phase, gc_bytes=data['gc_copied_bytes'],
                        rr_bytes=data['rr_copied_bytes'],
                        total_copy_bytes=data['gc_copied_bytes'] + data['rr_copied_bytes'],
                        gc_rounds=len(data['gc_rounds']), gc_resets=sum(r[2] for r in data['gc_rounds']),
                        rr_rounds=len(data['rr_rounds']), rr_resets=sum(r[1] for r in data['rr_rounds']),
                        process_seconds=(clock['end_ns'] - clock['begin_ns']) / 1e9)
            stages.append(item); phase_values[phase] = item
            resource = zone_metrics(read(folder / (phase + '-end.json')))
            remaining.append(dict(arm=arm, phase=phase, **{k: v for k, v in resource.items() if k != 'boundary'}))
        metrics.append(dict(arm=arm, windows=plan['windows'], requests=report['requests']['count'],
            business_gc_bytes=phase_values['run']['gc_bytes'], business_rr_bytes=phase_values['run']['rr_bytes'],
            business_copy_bytes=phase_values['run']['total_copy_bytes'],
            all_phase_copy_bytes=sum(x['total_copy_bytes'] for x in phase_values.values()),
            verification_copy_bytes=phase_values['verify']['total_copy_bytes'] + phase_values['reopen']['total_copy_bytes'],
            application_ops_per_second=report['application']['应用完成吞吐ops'],
            business_process_seconds=phase_values['run']['process_seconds'],
            coldmap_deferred=report['coldmap_deferred'], oaza_deferred=report['oaza_deferred']))
        latencies += [dict(arm=arm, **row) for row in report['application']['指标']]
        report['ending_layout_observations'] = {p: layout_metrics(folder, p) for p in ('load', 'run', 'verify')}
    output.mkdir(parents=True)
    for name, rows in [('维护与吞吐.csv', metrics), ('逐阶段维护.csv', stages),
                       ('应用端到端延迟.csv', latencies), ('阶段末端设备空间.csv', remaining)]:
        with (output / name).open('x', newline='') as f:
            writer = csv.DictWriter(f, list(rows[0])); writer.writeheader(); writer.writerows(rows)
    receipts = dict(passed=True, source=str(root), verification=str(evidence),
        verification_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
        full_workload_verified=proof['full_workload_verified'], metrics=metrics,
        final_layout_observations={arm: r['ending_layout_observations'] for arm, r in proof['reports'].items()},
        no_statistical_significance_claim=True,
        limitations=['single run per arm', 'policy-dependent post-load layouts and RC',
                     'g2 arm is original G2 placement with public valid-byte Greedy GC, not the full credit/score victim model',
                     'CRC32C is not a bytewise latest-version proof',
                     'verification reads can trigger large additional RR',
                     'GC-trigger policies differ by method; same-threshold mechanism isolation is not claimed'])
    (output / '汇总证据.json').write_text(json.dumps(receipts, ensure_ascii=False, indent=2) + '\n')
    lines = ['# 六臂共同负载：单轮结果', '',
             f"每臂{plan['windows']}窗口、{plan['requests']}条业务请求；独立装载1048576个4KiB值。", '',
             '|臂|业务GC MiB|业务RR MiB|验证读引发的复制 MiB|全部阶段复制 MiB|应用完成ops/s|',
             '|---|---:|---:|---:|---:|---:|']
    for r in metrics:
        lines.append('|%s|%.3f|%.3f|%.3f|%.3f|%.3f|' % (r['arm'], r['business_gc_bytes'] / (1 << 20),
            r['business_rr_bytes'] / (1 << 20), r['verification_copy_bytes'] / (1 << 20),
            r['all_phase_copy_bytes'] / (1 << 20), r['application_ops_per_second']))
    lines += ['', '业务阶段包含该进程内同步Flush、Compaction排空及关闭前维护；四阶段分别统计，'
              '验证扫描引发的维护不计入业务复制，也不从总账删除。', '',
              '本轮从全空设备独立执行各方法装载，逐请求逻辑输入一致；装载后的放置和RC并非同一物理检查点。'
              'OAZA维持15%启动，其余20%；差异属于本适配下的整套方法，不能全部归因于victim评分。', '',
              '本轮g2臂为既有G2放置配公共有效量Greedy GC，未启用完整Credit/Score受害区模型，'
              '不能用本轮g2结果代替完整联合机制的最终论文对照。', '',
              '单轮数值仅描述本负载。CRC32C与RocksDB校验和通过不能替代逐字节最新版本证明；'
              '不作统计显著性、正常并发或完整论文原环境复现结论。']
    (output / '结果与边界.md').write_text('\n'.join(lines) + '\n')
    print(output)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path); p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); summarize(a.root.resolve(), a.evidence.resolve(), a.output.resolve())
