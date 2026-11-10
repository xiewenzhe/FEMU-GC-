"""Serial independent build and functional recovery checks for new adapter forks."""
import argparse
import getpass
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_joint_validation_host as queue


def jobs():
    cold = 'joint-related-coldmap-build-bootstrap-v1-20261109'
    oaza = 'joint-related-oaza-gc-build-bootstrap-v1-20261109'
    return [
        dict(kind='build', method='coldmap', label=cold, overlay='coldmap-v6-20261109'),
        dict(kind='device', method='coldmap', label='joint-related-coldmap-device-bootstrap-joint-v1-20261109',
             build=cold, variant='artifact-survival', case='joint'),
        dict(kind='build', method='oaza_gc', label=oaza, overlay='oaza-gc-v8-20261109'),
        dict(kind='device', method='oaza_gc', label='joint-related-oaza-gc-device-bootstrap-recovery-v1-20261109',
             build=oaza, variant='adapted', case='recovery')]


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--detach', action='store_true')
    a = p.parse_args(); output = a.output.resolve()
    assert output.is_relative_to(HERE.parent / 'workspaces') and not output.exists()
    password = getpass.getpass('恢复启动验证认证（仅内存）：')
    output.mkdir(parents=True)
    (output / 'queue_source.py').write_bytes(Path(__file__).read_bytes())
    queue.atomic_json(output / 'plan.json', dict(jobs=jobs(),
        scope='functional regression and recovery-frame publication; not full-workload exhaustion acceptance',
        original_formulas_unchanged=True, concurrent_guests=1))
    if a.detach and not queue.detach_supervisor(output): sys.exit(0)
    queue.plan = jobs
    try: queue.execute(output, password, 14400)
    finally: password = None
