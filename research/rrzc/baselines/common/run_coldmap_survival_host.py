"""Resume only ColdMap artifact-survival using the already admitted printed binary."""
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
from reliable_startup import ReliableCalibrationRun
from analyze_comparison import analyze_arm, compare

LABEL = 'joint-related-coldmap-survival-v2-20261110'
PREVIOUS = common.IMAGE_DIR / 'joint-related-compare-bootstrap-coldmap-v1-20261110-results'
REFERENCE = common.IMAGE_DIR / 'joint-related-compare-full-v2-20261109-results'


def execute(output, password):
    state, _ = common.closed_archive(PREVIOUS / 'cold-printed')
    printed = PREVIOUS / 'cold-printed' / state['标签']
    printed_report = analyze_arm(printed, 12)
    build = PREVIOUS / 'build'
    _, proof = common.admit_build(build, 'coldmap')
    assert common.digest(printed / 'comparison_db_bench') == proof['binary_sha256']
    common.atomic_json(output / 'plan.json', dict(arm='cold-survival', variant='artifact-survival',
        build=str(build), printed_reference=str(printed), binary_sha256=proof['binary_sha256'],
        windows=12, requests=2260992, new_build=False, repeat_printed=False,
        all_methods_complete=False, original_device_and_resource_limits_preserved=True))
    common.CalibrationRun = ReliableCalibrationRun
    ReliableCalibrationRun.diagnose_legacy_control_path = (
        '/tmp/joint-related-compare-bootstrap-coldmap-v1-20261110-cold-survival-calibration-ssh.sock')
    args = SimpleNamespace(label=LABEL, build=build, windows=12)
    common.run_arm(args, output, password, 'cold-survival', 'coldmap', 'artifact-survival', reference_root=REFERENCE)
    state, _ = common.closed_archive(output / 'cold-survival')
    survival = output / 'cold-survival' / state['标签']
    report = analyze_arm(survival, 12)
    cross = compare(printed, survival, printed_report, report)
    common.atomic_json(output / 'printed-survival-audit.json', cross)
    common.record(output, stage='corrected_coldmap_pair_verified', current=None,
                  all_methods_complete=False, note='single full input per variant; no statistical generalization')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--detach', action='store_true'); args = p.parse_args()
    output = common.IMAGE_DIR / (LABEL + '-results'); assert not output.exists(), 'refuse overwrite'
    password = getpass.getpass('ColdMap survival 续跑认证（仅内存）：')
    output.mkdir(); frozen = output / 'entry-source'; frozen.mkdir()
    for source in (Path(__file__), HERE / 'reliable_startup.py', HERE / 'run_comparison_host.py',
                   HERE / 'analyze_comparison.py', HERE / 'comparison_guest.py', HERE / 'comparison_source.py'):
        (frozen / source.name).write_bytes(source.read_bytes())
    common.atomic_json(frozen / 'SHA256.json', {p.name: common.digest(p) for p in frozen.iterdir()})
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not common.detach_supervisor(output): sys.exit(0)
    try:
        common.record(output, supervisor_pid=os.getpid()); execute(output, password)
    except BaseException as error:
        common.record(output, stage='failed', error=repr(error)); raise
    finally: password = None
