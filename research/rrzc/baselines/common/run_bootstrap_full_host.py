"""Recheck one actual low-space failure workload after the isolated recovery fix."""
import argparse
import getpass
import json
from pathlib import Path
import signal
import sys
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_comparison_host as common
from comparison_source import make_payload, BASE
from run_joint_validation_host import job_complete

LABEL = 'joint-related-compare-bootstrap-oaza-v1-20261109'
REFERENCE = common.IMAGE_DIR / 'joint-related-compare-full-v2-20261109-results'
PRIOR_QUEUE = BASE / 'workspaces/recovery-bootstrap-20261109-v1/queue.json'
OVERLAY = 'oaza-gc-v8-20261109'


def execute(output, password):
    # All original regression jobs must finish before this failure reproduction.
    deadline = time.monotonic() + 7200
    common.record(output, stage='waiting_for_functional_regression', current=None,
                  scope='one corrected OAZA adapted arm; not a new all-method performance comparison')
    while True:
        state = json.loads(PRIOR_QUEUE.read_text())
        if state['status'] == 'failed': raise RuntimeError('functional regression failed; no full workload started')
        if state['status'] == 'complete': break
        if time.monotonic() >= deadline: raise TimeoutError('functional regression deadline')
        time.sleep(20)
    for job in state['jobs']:
        assert job_complete(common.IMAGE_DIR / (job['label'] + '-results'), job['kind'])
    build = output / 'build'; build.mkdir()
    folder = build / 'oaza_gc'; folder.mkdir()
    common.wait_slot(output)
    payload = folder / 'payload.tar.gz'
    make_payload('oaza_gc', payload, BASE / 'workspaces' / OVERLAY)
    common.record(output, stage='building_corrected_common_client', current='oaza_gc')
    common.native.execute(SimpleNamespace(label=LABEL + '-build-oaza', method='oaza_gc'), password, folder, payload)
    common.admit_build(build, 'oaza_gc')
    common.record(output, stage='common_client_build_verified', current=None)
    args = SimpleNamespace(label=LABEL, build=build, windows=12)
    common.run_arm(args, output, password, 'oaza-adapted', 'oaza_gc', 'adapted', reference_root=REFERENCE)
    common.record(output, stage='corrected_arm_verified', current=None,
                  all_methods_complete=False, full_six_arm_verified=False,
                  note='corrected program and one full workload only; old failed archives remain unchanged')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--detach', action='store_true')
    p.add_argument('--source-batches', action='store_true', help='test source-contiguous OAZA v9 after its separate functional regression')
    a = p.parse_args()
    if a.source_batches:
        LABEL = 'joint-related-compare-bootstrap-oaza-v2-20261109'
        OVERLAY = 'oaza-gc-v9-20261109'
        PRIOR_QUEUE = BASE / 'workspaces/recovery-batches-20261109-v1/queue.json'
    output = common.IMAGE_DIR / (LABEL + '-results')
    assert not output.exists(), 'refuse overwrite'
    password = getpass.getpass('低空间恢复完整输入复验认证（仅内存）：')
    output.mkdir()
    freeze = output / 'entry-source'; freeze.mkdir()
    files = [Path(__file__), HERE / 'comparison_source.py', HERE / 'comparison_guest.py',
             HERE / 'run_comparison_host.py', HERE / 'analyze_comparison.py']
    for source in files: (freeze / source.name).write_bytes(source.read_bytes())
    common.atomic_json(freeze / 'SHA256.json', {p.name: common.digest(p) for p in freeze.iterdir()})
    common.atomic_json(output / 'plan.json', dict(
        label=LABEL, corrected_overlay=OVERLAY, prior_workload=str(REFERENCE),
        repeated_greedy=False, windows=12, requests=2260992, GC_threshold=15, RR_threshold=1024,
        observation_and_input_unchanged=True, statistics_or_final_comparison=False,
        purpose='actual previously failing low-space DB reopen; archive and exit on success or failure'))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if a.detach and not common.detach_supervisor(output): sys.exit(0)
    try:
        common.record(output, supervisor_pid=__import__('os').getpid())
        execute(output, password)
    except BaseException as error:
        common.record(output, stage='failed', error=repr(error)); raise
    finally: password = None
