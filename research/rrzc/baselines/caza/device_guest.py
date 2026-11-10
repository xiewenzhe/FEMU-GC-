"""Small CAZA placement/reopen test on this run's verified fresh FEMU namespace only."""
import hashlib
import csv
import json
import os
import re
from pathlib import Path
import subprocess
import sys


def run(target):
    assert os.geteuid() == 0
    owner = target.stat()
    assert not (target / 'device_complete.json').exists()
    proof = json.loads((target / 'build_complete.json').read_text())
    test_case = json.loads((target / 'test_case.json').read_text())['case']
    assert test_case in ('basic', 'direct', 'rr', 'pressure', 'gc', 'fault', 'nospace')
    binary = target / 'caza_probe'
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == proof['binary_sha256']
    if test_case == 'fault':
        assert hashlib.sha256((target / 'fault_io.so').read_bytes()).hexdigest() == proof['fault_library_sha256']
    def query(argv): return subprocess.check_output(argv, text=True)
    assert query(['blockdev', '--getsize64', '/dev/nvme0n1']).strip() == '8589934592'
    ctrl = json.loads(query(['nvme', 'id-ctrl', '/dev/nvme0n1', '-o', 'json']))
    assert str(ctrl['mn']).strip().startswith('FEMU'), 'refuse a non-FEMU controller'
    initial = json.loads(query(['nvme', 'zns', 'report-zones', '/dev/nvme0n1', '-o', 'json']))
    assert len(initial['zone_list']) == 64
    assert all(z['state'] == 'EMPTY' and z['wp'] == z['slba'] for z in initial['zone_list'])
    identify = json.loads(query(['nvme', 'zns', 'id-ns', '/dev/nvme0n1', '-o', 'json']))
    assert identify['mor'] + 1 == 8 and identify['mar'] + 1 == 12
    (target / 'initial_device.json').write_text(json.dumps(dict(
        controller=ctrl, zones=initial, identify=identify), indent=2) + '\n')
    scheduler = Path('/sys/block/nvme0n1/queue/scheduler')
    before = scheduler.read_text(); scheduler.write_text('mq-deadline\n')
    assert '[mq-deadline]' in scheduler.read_text()
    (target / 'scheduler.json').write_text(json.dumps(dict(before=before,
        during=scheduler.read_text(), scope='only the fresh simulated guest namespace')) + '\n')
    env = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', LANG='C.UTF-8', ZENFS_RELATED_WORK='caza',
        CAZA_TEST_CASE=test_case,
        ZENFS_RR_STRICT='1', ZENFS_RR_ENABLE='1', ZENFS_MAINT_DIAG='1', ZENFS_ALLOC_DIAG='1',
        ZENFS_ALLOC_FALLBACK='1', ZENFS_MIGRATE_RESERVE='1', ZENFS_RRZC_CONTENT_GUARD='1',
        ZENFS_RRZC_MIGRATION_SPARE='1', ZENFS_RRZC_EMPTY_RESERVE='1',
        ZENFS_RRZC_RESERVE_TARGET='1', ZENFS_RRZC_SINGLE_RECOVERY='1', ZENFS_RR_FAIR_QUEUE='1',
        ZENFS_RRZC_SIMPLE_MODE='valid', ZENFS_RRZC_RECLAIM_BYTES='0', ZENFS_RRZC_V1_SHADOW='1')
    def execute(argv, name, active=None):
        (target / (name + '.command.json')).write_text(json.dumps(dict(argv=argv, env=active or env)) + '\n')
        with (target / (name + '.log')).open('x') as out:
            result = subprocess.run(argv, env=active or env, stdout=out,
                                    stderr=subprocess.STDOUT, timeout=600 if test_case in ('gc', 'nospace') else 180)
        (target / (name + '.exit')).write_text(str(result.returncode) + '\n')
        assert result.returncode == 0, name
    (target / 'aux').mkdir()
    try:
        mkfs = ['/home/femu/rocksdb/plugin/zenfs/util/zenfs', 'mkfs', '--zbd=nvme0n1',
                '--aux_path=' + str(target / 'aux')]
        if test_case == 'gc': mkfs += ['--enable_gc']
        execute(mkfs, 'mkfs')  # Only the verified fresh simulator is formatted.
        for mode in ('device', 'verify'):
            active = dict(env, ZENFS_RRZC_PLACEMENT_TRACE=str(target / (mode + '-composition.bin')))
            if test_case == 'fault': active['LD_PRELOAD'] = str(target / 'fault_io.so')
            execute([str(binary), mode, str(target)], mode, active)
        (target / 'final_zones.json').write_text(query(
            ['nvme', 'zns', 'report-zones', '/dev/nvme0n1', '-o', 'json']))
        log = (target / 'zenfs-device.log').read_text()
        assert 'CAZA target: stage=empty' in log, 'no actual empty-zone allocation'
        if test_case not in ('pressure', 'gc', 'nospace'):
            assert 'CAZA target: stage=overlap' in log, 'no actual next-level overlap allocation'
        assert 'level=1' in '\n'.join(line for line in log.splitlines()
                                      if 'CAZA target:' in line or 'CAZA fallback:' in line), \
            'no actual level-one SST write during compaction'
        assert (target / 'device-mapping.csv').read_bytes() == (target / 'verify-mapping.csv').read_bytes(), \
            'live SST/level/extent mapping changed across remount'
        with (target / 'device-mapping.csv').open() as stream:
            native = list(csv.DictReader(stream))
        initial_log = log.split('RR 完成：', 1)[0] if test_case in ('rr', 'fault') else log
        targets = re.findall(r'CAZA target: stage=overlap file=(\d+) level=(\d+) zone=(\d+)', initial_log)
        if test_case not in ('pressure', 'gc', 'nospace'): assert targets
        else: targets = []  # The GC preparation intentionally replaces its original L1 input SST.
        for number, level, zone in targets:
            observed = native
            if test_case in ('rr', 'fault'):
                with (target / 'before-rr-mapping.csv').open() as stream:
                    observed = list(csv.DictReader(stream))
            assert any(row['sst'] == number and row['level'] == level and row['zone'] == zone
                       for row in observed), 'reported overlap allocation absent from actual live mapping'
            assert any(int(row['level']) == int(level) + 1 and row['zone'] == zone for row in observed), \
                'reported overlap target has no live SST from the next level'
        rr_result = None
        pressure_result = None
        gc_result = None
        fault_result = None
        nospace_result = None
        if test_case in ('pressure', 'gc', 'nospace'):
            def rows(name):
                with (target / name).open() as stream: return list(csv.DictReader(stream))
            mapping = rows('pressure-before-compaction.csv')
            metadata = rows('pressure-files.csv'); assert len(metadata) == 25
            assert all(row['level'] == '0' for row in metadata)
            prefix = log.split('CAZA pressure baseline complete', 1)[0]
            nearest = re.findall(r'CAZA target: stage=nearest file=(\d+) level=(\d+) zone=(\d+)', prefix)
            assert nearest, 'no real nearest-key resource fallback'
            for number, level, zone in nearest:
                request = next(row for row in metadata if row['sst'] == number)
                older = [row for row in metadata if int(row['sst']) < int(number) and row['level'] == level]
                assert older
                distance = lambda row: max(0, int.from_bytes(request['smallest'].encode(), 'big') -
                                           int.from_bytes(row['largest'].encode(), 'big'),
                                           int.from_bytes(row['smallest'].encode(), 'big') -
                                           int.from_bytes(request['largest'].encode(), 'big'))
                best = min(older, key=lambda row: (distance(row), int(row['sst'])))
                assert any(row['sst'] == best['sst'] and row['zone'] == zone for row in mapping), \
                    'nearest allocation differs from the actual closest prior SST mapping'
                assert any(row['sst'] == number and row['zone'] == zone for row in mapping)
            liza = re.findall(r'CAZA fallback: stage=liza file=(\d+) level=(\d+)[^\n]*\n'
                              r'.*?Allocating zone\(new=\d+\) start: 0x([0-9a-f]+)', prefix, re.S)
            assert liza, 'no actual legacy LIZA allocation after CAZA fallback'
            after = rows('pressure-after-compaction.csv')
            for number, level, hex_zone in liza:
                assert level == '1' and any(row['sst'] == number and row['level'] == level and
                    int(row['zone']) == int(hex_zone, 16) for row in after), 'LIZA target absent from native L1 mapping'
            zones = rows('pressure-before-zones.csv')
            assert sum(row['wp'] == row['zone'] for row in zones) > 0, 'fixture unexpectedly exhausted all physical empty Zones'
            pressure_result = dict(nearest_targets=nearest, liza_targets=liza,
                                   boundary='unchanged Active resource quota, physical empty Zones still available')
            if test_case == 'gc':
                assert 'ZC 统计：' in log and 'ZC 迁移失败：' not in log
                assert 'RR 完成：' not in log, 'GC fixture unexpectedly exercised RR'
                selected = re.findall(r'GC 选区汇总：检查空闲比例=(\d+)%[^\n]*选中Zone=(\d+)，迁移片段=(\d+)', log)
                assert any(int(free) <= 20 and int(count) > 0 and int(extents) > 0
                           for free, count, extents in selected), 'GC did not use its original low-space qualification'
                state = rows('gc-state.csv'); assert len(state) == 1
                assert int(state[0]['generation_after']) == int(state[0]['generation_before']) + 1
                assert state[0]['source'] != state[0]['target']
                gc_result = dict(state=state[0], original_space_gate_verified=True,
                                 no_rr_observed=True, filler_files=len(rows('gc-fillers.csv')))
        if test_case == 'direct':
            assert re.search(r'New writable file: .*\.sst direct: 1', log), 'actual SST writes were not direct'
        if test_case in ('rr', 'fault'):
            with (target / 'rr-state.csv').open() as stream:
                before, after = list(csv.DictReader(stream))
            assert before['source'] != after['mapped'] and after['source_rc'] == '0'
            assert int(after['threshold_events']) == int(before['threshold_events']) + 1
            assert int(after['highest_rc']) <= 1024
            assert log.count('RR 完成：') == 1, 'expected one natural RR'
            post = log.split('RR 完成：', 1)[1]
            selected = re.findall(r'CAZA target: stage=(\w+) file=(\d+) level=(\d+) zone=(\d+)', post)
            assert len(selected) == 1, 'missing/ambiguous post-RR placement'
            selected_stage, number, level, zone = selected[0]
            assert any(row['sst'] == number and row['level'] == level and row['zone'] == zone for row in native)
            rejected = re.findall(r'CAZA candidate unavailable: stage=overlap file=(\d+) zone=(\d+) generation=(\d+) reason=(\S+)', post)
            if selected_stage == 'overlap':
                assert zone == after['mapped'], 'post-RR overlap placement used a stale mapping'
            else:
                assert selected_stage == 'empty' and any(n == number and z == after['mapped'] and
                    generation == after['mapped_host_generation'] and reason in ('busy', 'full')
                    for n, z, generation, reason in rejected), 'fallback did not verify the current L1 candidate'
                assert not any(z == before['source'] for n, z, generation, reason in rejected), 'stale pre-RR candidate used'
            rr_result = dict(before=before, after=after, post_rr_target=int(zone),
                             post_rr_stage=selected_stage, rejected_current_candidates=rejected)
            if test_case == 'fault':
                assert (target / 'before-rr-mapping.csv').read_bytes() == (target / 'fault-mapping.csv').read_bytes()
                with (target / 'fault-state.csv').open() as stream: states = list(csv.DictReader(stream))
                assert len(states) == 1 and states[0]['failures'] == '1'
                assert int(states[0]['failed_write_offset']) >= 3 * (128 << 20)
                assert '复制成功=0' in log and (target / 'migration-error.txt').read_text().strip()
                fault_result = dict(state=states[0], original_error=(target / 'migration-error.txt').read_text(),
                                    failed_mapping_unchanged=True, ordinary_retry_completed=True,
                                    scope='one pwrite EIO to this simulated namespace during RR copy; no crash/metadata-failure injection')
        if test_case == 'nospace':
            with (target / 'nospace-state.csv').open() as stream: states = list(csv.DictReader(stream))
            assert len(states) == 1 and all(states[0][key] == '1' for key in
                ('filler_nospace', 'sst_nospace', 'reopened_committed_values', 'recovery_flush'))
            assert int(states[0]['generation_after']) == int(states[0]['generation_before']) + 1
            assert (target / 'nospace-filler-error.txt').read_text().strip()
            assert (target / 'nospace-sst-error.txt').read_text().strip()
            assert 'Zone 分配返回空：' in log and 'RR 完成：' not in log
            nospace_result = dict(state=states[0], filler_error=(target / 'nospace-filler-error.txt').read_text(),
                                 sst_error=(target / 'nospace-sst-error.txt').read_text(),
                                 scope='allocator-eligible capacity exhausted; protected reserve/metadata capacity not forcibly consumed')
        assert '协同池取得目标：' not in log, 'original cooperative pool was entered'
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == proof['binary_sha256']
        (target / 'device_complete.json').write_text(json.dumps(dict(
            passed=True, case=test_case, keys=6144 if test_case in ('pressure', 'gc', 'nospace') else 2048,
            controller='FEMU', Open=8, Active=12, capacity_GiB=8,
            latest_values_verified=True, checksums_verified=True, remounted=True,
            empty_target_observed=True, overlap_target_observed=test_case not in ('pressure', 'gc', 'nospace'),
            compaction_rewrite_observed=True, native_live_mapping_verified=True,
            actual_live_co_placement_verified=test_case not in ('pressure', 'gc', 'nospace'),
            direct_io_verified=test_case == 'direct', natural_rr_verified=test_case in ('rr', 'fault'), rr=rr_result,
            pressure=pressure_result, natural_gc_verified=test_case == 'gc', gc=gc_result,
            migration_copy_error_verified=test_case == 'fault', fault=fault_result,
            eligible_space_exhaustion_recovery_verified=test_case == 'nospace', nospace=nospace_result,
            gc_enabled=test_case == 'gc', performance_experiment=False,
            limitations=['not a full workload', 'no process-crash or metadata-write-failure recovery exercise',
                         'initial RC/pending queues not directly sampled']), indent=2) + '\n')
    finally:
        # Archive even root-owned error/trace files without changing their contents.
        for path in target.rglob('*'):
            assert not path.is_symlink()
            if path.is_file(): os.chown(path, owner.st_uid, owner.st_gid)


if __name__ == '__main__': run(Path(sys.argv[1]).resolve())
