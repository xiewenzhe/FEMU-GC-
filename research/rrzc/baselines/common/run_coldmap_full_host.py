"""Build the corrected ColdMap client once, then verify its two full input arms."""
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
from comparison_source import make_payload, BASE
from run_joint_validation_host import job_complete

LABEL = 'joint-related-compare-bootstrap-coldmap-v1-20261110'
REFERENCE = common.IMAGE_DIR / 'joint-related-compare-full-v2-20261109-results'
JOBS = (('cold-printed', 'printed'), ('cold-survival', 'artifact-survival'))


def execute(output, password):
    queue = json.loads((BASE / 'workspaces/recovery-bootstrap-20261109-v1/queue.json').read_text())
    assert queue['status'] == 'complete', 'functional regression must complete first'
    for job in queue['jobs']:
        if job['method'] == 'coldmap':
            assert job_complete(common.IMAGE_DIR / (job['label'] + '-results'), job['kind'])
    common.record(output, stage='waiting_for_shared_slot', completed=[], failed_arms=[])
    common.wait_slot(output)
    build = output / 'build'; build.mkdir(); folder = build / 'coldmap'; folder.mkdir()
    payload = folder / 'payload.tar.gz'
    make_payload('coldmap', payload, BASE / 'workspaces/coldmap-v6-20261109')
    common.record(output, stage='building_corrected_common_client', current='coldmap')
    common.native.execute(SimpleNamespace(label=LABEL + '-build-coldmap', method='coldmap'), password, folder, payload)
    common.admit_build(build, 'coldmap')
    args = SimpleNamespace(label=LABEL, build=build, windows=12)
    for arm, variant in JOBS:
        try:
            common.run_arm(args, output, password, arm, 'coldmap', variant, reference_root=REFERENCE)
        except Exception:
            # Continue only a complete, stopped and byte-verified failed arm.
            common.retain_failed_arm(args, output, output, arm, 'coldmap', variant)
    state = json.loads((output / 'comparison-state.json').read_text())
    common.record(output, stage='complete_with_failures' if state['failed_arms'] else 'corrected_arms_verified',
        current=None, all_methods_complete=False, full_six_arm_verified=False,
        note='two corrected ColdMap directions, single full input each; not final six-arm statistics')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--detach', action='store_true'); args = p.parse_args()
    out = common.IMAGE_DIR / (LABEL + '-results'); assert not out.exists(), 'refuse overwrite'
    password = getpass.getpass('ColdMap 两版完整复验认证（仅内存）：')
    out.mkdir(); frozen = out / 'entry-source'; frozen.mkdir()
    for source in [Path(__file__), HERE / 'comparison_source.py', HERE / 'comparison_guest.py',
                   HERE / 'run_comparison_host.py', HERE / 'analyze_comparison.py', HERE / 'comparison_failure.py']:
        (frozen / source.name).write_bytes(source.read_bytes())
    common.atomic_json(frozen / 'SHA256.json', {p.name: common.digest(p) for p in frozen.iterdir()})
    common.atomic_json(out / 'plan.json', dict(overlay='coldmap-v6-20261109', arms=JOBS,
        windows=12, requests_per_arm=2260992, GC_threshold=20, RR_threshold=1024,
        same_binary_for_both_directions=True, new_greedy_repeats=False,
        reference=str(REFERENCE), final_six_arm_or_statistical_comparison=False))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not common.detach_supervisor(out): sys.exit(0)
    try:
        common.record(out, supervisor_pid=os.getpid()); execute(out, password)
    except BaseException as error:
        common.record(out, stage='failed', error=repr(error)); raise
    finally: password = None
