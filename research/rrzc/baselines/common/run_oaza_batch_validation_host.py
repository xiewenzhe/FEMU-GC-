"""Compile and regress the source-contiguous OAZA execution fork serially."""
import argparse
import getpass
from pathlib import Path
import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_joint_validation_host as queue


def jobs():
    build = 'joint-related-oaza-gc-build-batches-v1-20261109'
    return [dict(kind='build', method='oaza_gc', label=build, overlay='oaza-gc-v9-20261109'),
            dict(kind='device', method='oaza_gc', label='joint-related-oaza-gc-device-batches-recovery-v1-20261109',
                 build=build, variant='adapted', case='recovery')]


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--detach', action='store_true'); a = p.parse_args(); out = a.output.resolve()
    assert out.is_relative_to(HERE.parent / 'workspaces') and not out.exists()
    password = getpass.getpass('OAZA 分源迁移验证认证（仅内存）：')
    out.mkdir(parents=True); (out / 'queue_source.py').write_bytes(Path(__file__).read_bytes())
    queue.atomic_json(out / 'plan.json', dict(jobs=jobs(), concurrent_guests=1,
        scope='functional recovery regression; source order only, original selected set and costs',
        low_space_full_workload_verified=False))
    if a.detach and not queue.detach_supervisor(out): sys.exit(0)
    queue.plan = jobs
    try: queue.execute(out, password, 14400)
    finally: password = None
