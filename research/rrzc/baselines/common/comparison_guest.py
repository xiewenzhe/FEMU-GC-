"""Run one shared workload on an independently verified fresh FEMU namespace."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(1 << 20), b''): h.update(data)
    return h.hexdigest()


def run(target):
    assert os.geteuid() == 0
    from calibration_guest import blocks
    from strict_stats_guest import read_stats
    plan = json.loads((target / 'comparison_plan.json').read_text())
    assert 1 <= plan['windows'] <= 12 and plan['arm'] in (
        'g2', 'caza', 'oaza-literal', 'oaza-adapted', 'cold-printed', 'cold-survival')
    proof = json.loads((target / 'comparison_build.json').read_text())
    binary = target / 'comparison_db_bench'
    assert proof['passed'] and sha(binary) == proof['binary_sha256']
    def query(args): return subprocess.check_output(args, text=True)
    assert query(['blockdev', '--getsize64', '/dev/nvme0n1']).strip() == '8589934592'
    assert not query(['lsblk', '-nr', '-o', 'MOUNTPOINTS', '/dev/nvme0n1']).strip()
    ctrl = json.loads(query(['nvme', 'id-ctrl', '/dev/nvme0n1', '-o', 'json']))
    assert ctrl['mn'].strip().startswith('FEMU')
    identify = json.loads(query(['nvme', 'zns', 'id-ns', '/dev/nvme0n1', '-o', 'json']))
    assert identify['mor'] + 1 == 8 and identify['mar'] + 1 == 12
    def snapshot(name):
        zones = json.loads(query(['nvme', 'zns', 'report-zones', '/dev/nvme0n1', '-o', 'json']))
        data = dict(zones=zones, identify=identify, blocks=blocks(), strict=read_stats())
        (target / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False) + '\n')
        return data
    initial = snapshot('initial')
    assert len(initial['zones']['zone_list']) == 64
    assert all(z['state'] == 'EMPTY' and z['slba'] == z['wp'] for z in initial['zones']['zone_list'])
    assert all(b['RC'] == b['十秒增量'] == b['三十秒增量'] == b['代际'] == 0
               for b in initial['blocks'])
    assert initial['strict']['阈值触发事件'] == 0
    scheduler = Path('/sys/block/nvme0n1/queue/scheduler')
    before = scheduler.read_text(); scheduler.write_text('mq-deadline\n')
    assert '[mq-deadline]' in scheduler.read_text()
    (target / 'scheduler.json').write_text(json.dumps(dict(before=before, during=scheduler.read_text())))
    env = dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin', LANG='C.UTF-8',
        ZENFS_RR_STRICT='1', ZENFS_RR_ENABLE='1', ZENFS_MAINT_DIAG='1', ZENFS_ALLOC_DIAG='1',
        ZENFS_ALLOC_FALLBACK='1', ZENFS_MIGRATE_RESERVE='1', ZENFS_RRZC_CONTENT_GUARD='1',
        ZENFS_RRZC_MIGRATION_SPARE='1', ZENFS_RRZC_EMPTY_RESERVE='1', ZENFS_RRZC_RESERVE_TARGET='1',
        ZENFS_RRZC_SINGLE_RECOVERY='1', ZENFS_RR_FAIR_QUEUE='1', ZENFS_RRZC_SIMPLE_MODE='valid',
        ZENFS_RRZC_RECLAIM_BYTES='0', ZENFS_RRZC_V1_SHADOW='1', RRZC_CAL_TLS_SEED='1',
        RRZC_CAL_LOAD_CHUNK='2048', RRZC_CAL_COMMON_OBSERVATION='1', RRZC_CAL_GREEDY_EXECUTE='1',
        RRZC_SST_LIFETIME_INTERLEAVED='1', RRZC_SST_LIFETIME_PERSISTENT_H='1',
        RRZC_RELATED_WINDOWS=str(plan['windows']))
    if plan['arm'] == 'g2': env['ZENFS_RRZC_PLACEMENT_MODE'] = 'G2'
    else:
        env['ZENFS_RELATED_WORK'] = plan['method']
        if plan['method'] == 'oaza_gc': env['OAZA_GC_VARIANT'] = plan['variant']
        if plan['method'] == 'coldmap': env['COLDMAP_FACTOR_DIRECTION'] = plan['variant']
    common = ['--fs_uri=zenfs://dev:nvme0n1', '--db=/rrzc-dbbench', '--num=1048576',
        '--value_size=4096', '--compression_type=none', '--disable_wal=1', '--cache_size=0',
        '--row_cache_size=0', '--use_direct_reads=1', '--use_direct_io_for_flush_and_compaction=1',
        '--verify_checksum=1', '--write_buffer_size=16777216', '--max_write_buffer_number=2',
        '--max_background_jobs=2', '--max_background_flushes=1', '--max_background_compactions=1',
        '--num_high_pri_threads=1', '--num_low_pri_threads=1', '--file_opening_threads=1',
        '--target_file_size_base=16777216', '--max_bytes_for_level_base=134217728',
        '--level0_file_num_compaction_trigger=2', '--level0_slowdown_writes_trigger=4',
        '--level0_stop_writes_trigger=8', '--bloom_bits=10', '--histogram=1', '--threads=1',
        '--seed=20261023', '--compaction_pri=3', '--level_compaction_dynamic_level_bytes=0',
        '--benchmark_write_rate_limit=0', '--rate_limiter_bytes_per_sec=0']
    (target / 'common-options.json').write_text(json.dumps(common))
    def execute(args, name, active):
        (target / 'phase.txt').write_text(name)
        (target / (name + '.command.json')).write_text(json.dumps(dict(argv=args, env=active)))
        with (target / (name + '.log')).open('x') as log:
            p = subprocess.run(args, env=active, stdout=log, stderr=subprocess.STDOUT, timeout=2400)
        (target / (name + '.exit')).write_text(str(p.returncode))
        if name != 'mkfs':
            assert (target / (name + '.zenfs.log')).is_file(), 'missing explicit ZenFS log'
            rock = target / 'aux/rrzc-dbbench/LOG'
            if rock.exists(): shutil.copyfile(rock, target / (name + '.rocksdb.log'))
        assert p.returncode == 0, name + ' failed'
    (target / 'aux').mkdir()
    owner = target.stat()
    try:
        execute(['/home/femu/rocksdb/plugin/zenfs/util/zenfs', 'mkfs', '--zbd=nvme0n1',
                 '--aux_path=' + str(target / 'aux'), '--enable_gc'], 'mkfs', env)
        for phase, benchmarks in (
                ('load', 'rrzccalload,rrzclayout'),
                ('run', 'rrzctick,rrzclayout,rrzctick,rrzcsstlifetime,rrzctick,rrzclayout'),
                ('verify', 'rrzcrelateddigest,verifychecksum,rrzclayout'),
                ('reopen', 'rrzcrelateddigest,verifychecksum')):
            active = dict(env, ZENFS_RRZC_PLACEMENT_TRACE=str(target / (phase + '-composition.bin')),
                          RRZC_RELATED_ZENFS_LOG=str(target / (phase + '.zenfs.log')),
                          RRZC_PHASE_TIME=str(target / (phase + '-wait.bin')),
                          RRZC_DB_HOTSPOT='1', RRZC_DB_LAYOUT_PREFIX=str(target / (phase + '-layout')))
            if phase == 'run':
                active.update(RRZC_CAL_REQUEST_TRACE=str(target / 'requests.bin'),
                              RRZC_END_TO_END=str(target / 'application.bin'))
            begin = time.monotonic_ns()
            execute([str(binary), *common, '--use_existing_db=' + ('0' if phase == 'load' else '1'),
                     '--benchmarks=' + benchmarks, '--reads=' + str(plan['windows'] * 65536),
                     '--writes=' + str(plan['windows'] * 122880)], phase, active)
            end = time.monotonic_ns()
            (target / (phase + '-clock.json')).write_text(json.dumps(dict(begin_ns=begin, end_ns=end)))
            snapshot(phase + '-end')
        generated = re.findall(r'Related generated input: keys=(\d+) crc32c=(\d+)',
                               (target / 'load.log').read_text())
        digests = [re.findall(r'Related logical digest: keys=(\d+) bytes=(\d+) crc32c=(\d+)',
                             (target / (p + '.log')).read_text()) for p in ('verify', 'reopen')]
        assert len(generated) == 1 and len(digests[0]) == len(digests[1]) == 1
        assert generated[0][0] == digests[0][0][0] == '1048576' and digests[0] == digests[1]
        assert sha(binary) == proof['binary_sha256']
        (target / 'comparison_complete.json').write_text(json.dumps(dict(
            passed=True, arm=plan['arm'], windows=plan['windows'], input_generated=generated[0],
            final_digest=digests[0][0], reopened_digest_equal=True, binary_sha256=sha(binary),
            common_client_files=proof['client_files'], performance_claim=False), indent=2) + '\n')
    finally:
        for p in target.rglob('*'):
            if p.is_file() and not p.is_symlink(): os.chown(p, owner.st_uid, owner.st_gid)
        scheduler.write_text(re.search(r'\[([^]]+)\]', before).group(1) + '\n')


if __name__ == '__main__': run(Path(sys.argv[1]).resolve())
