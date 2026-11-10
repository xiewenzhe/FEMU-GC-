"""Verify the remaining literal OAZA arm with the already admitted v9 binary."""
import argparse
import getpass
import json
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_comparison_host as common
from comparison_source import BASE
from reliable_startup import ReliableCalibrationRun

LABEL = 'joint-related-oaza-literal-v3-20261110'
PREVIOUS = common.IMAGE_DIR / 'joint-related-compare-bootstrap-oaza-v2-20261109-results'
REFERENCE = common.IMAGE_DIR / 'joint-related-compare-full-v2-20261109-results'
OVERLAY = BASE / 'workspaces/oaza-gc-v9-20261109'


def admit_reused_build(build):
    folder, proof = common.admit_build(build, 'oaza_gc')
    identity = json.loads((folder / 'overlay_identity.json').read_text())
    assert identity['method'] == 'oaza_gc'
    assert identity['recovery_bootstrap']['policy_formulas_unchanged']
    assert identity['migration_batch_execution']['selected_set_and_cost_unchanged']
    frozen = json.loads((OVERLAY / 'overlay_identity.json').read_text())
    assert identity['source_sha256'] == frozen['source_sha256'], 'build is not the frozen v9 recovery/batch fork'
    assert all(common.digest(OVERLAY / 'src/rocksdb' / name) == sha
               for name, sha in frozen['source_sha256'].items()), 'v9 source changed'
    return folder, proof


def execute(output, password):
    build = PREVIOUS / 'build'
    _, proof = admit_reused_build(build)
    state, _ = common.closed_archive(PREVIOUS / 'oaza-adapted')
    assert json.loads((PREVIOUS / 'oaza-adapted/阶段归档.json').read_text())['业务成功']
    adapted = PREVIOUS / 'oaza-adapted' / state['标签']
    assert common.digest(adapted / 'comparison_db_bench') == proof['binary_sha256']
    common.atomic_json(output / 'plan.json', dict(arm='oaza-literal', method='oaza_gc', variant='literal',
        build=str(build), overlay=str(OVERLAY), binary_sha256=proof['binary_sha256'],
        adapted_reference=str(adapted), original_reference=str(REFERENCE), windows=12, requests=2260992,
        GC_threshold=15, RR_threshold=1024, new_build=False, repeat_successful_arms=False,
        all_methods_complete=False, statistics_or_final_performance_comparison=False))
    common.CalibrationRun = ReliableCalibrationRun
    args = SimpleNamespace(label=LABEL, build=build, windows=12)
    try:
        common.run_arm(args, output, password, 'oaza-literal', 'oaza_gc', 'literal', reference_root=REFERENCE)
    except Exception:
        parent = output / 'oaza-literal'
        # A startup failure has no business tar. Do not mask it with a second
        # failure-receipt exception or mislabel it as a completed workload.
        saved = json.loads((parent / '状态.json').read_text()) if (parent / '状态.json').exists() else {}
        stage = json.loads((parent / '阶段归档.json').read_text()) if (parent / '阶段归档.json').exists() else {}
        if stage.get('完整') and not stage.get('业务成功', True) and saved.get('镜像处理', {}).get('archive'):
            failure = common.failure_receipt(parent, dict(arm='oaza-literal', method='oaza_gc', variant='literal', windows=12))
            assert failure['binary_sha256'] == proof['binary_sha256']
            common.atomic_json(output / 'literal-failure-audit.json', failure)
        raise
    common.record(output, stage='corrected_literal_arm_verified', current=None, all_methods_complete=False,
        note='single full input; unchanged literal cost variant with separately identified recovery/batch execution adaptations')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--detach', action='store_true')
    args = parser.parse_args()
    output = common.IMAGE_DIR / (LABEL + '-results'); assert not output.exists(), 'refuse overwrite'
    password = getpass.getpass('OAZA literal 完整复验认证（仅内存）：')
    output.mkdir(); frozen = output / 'entry-source'; frozen.mkdir()
    for source in (Path(__file__), HERE / 'reliable_startup.py', HERE / 'run_comparison_host.py',
                   HERE / 'comparison_source.py', HERE / 'comparison_guest.py', HERE / 'analyze_comparison.py'):
        (frozen / source.name).write_bytes(source.read_bytes())
    common.atomic_json(frozen / 'SHA256.json', {p.name: common.digest(p) for p in frozen.iterdir()})
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not common.detach_supervisor(output): sys.exit(0)
    try:
        common.record(output, supervisor_pid=os.getpid()); execute(output, password)
    except BaseException as error:
        common.record(output, stage='failed', error=repr(error)); raise
    finally: password = None
