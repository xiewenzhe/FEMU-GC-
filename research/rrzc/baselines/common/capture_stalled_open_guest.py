"""Capture a confirmed failed DB open, then request termination of that PID only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path); parser.add_argument('pid', type=int)
    args = parser.parse_args(); target = args.target.resolve(); proc = Path('/proc') / str(args.pid)
    assert os.geteuid() == 0 and target.parent == Path('/home/femu')
    expected = str(target / 'comparison_db_bench')
    command = (proc / 'cmdline').read_bytes().split(b'\0')
    assert command[0].decode() == expected and b'--use_existing_db=1' in command
    assert (target / 'phase.txt').read_text() == 'verify'
    log = target / 'aux/rrzc-dbbench/LOG'
    assert 'DB::Open() failed: IO error: No space left on device' in log.read_text()
    assert time.time() - log.stat().st_mtime > 60, 'allow normal shutdown first'
    evidence = dict(pid=args.pid, command=[x.decode() for x in command if x],
                    known_failure='DB::Open NoSpace followed by stalled shutdown',
                    action='SIGTERM to verified failed verification process only; no SIGKILL',
                    full_maintenance_trace_claimed=False, threads={})
    for task in (proc / 'task').iterdir():
        thread = {}
        for name in ('comm', 'status', 'wchan', 'stack', 'syscall'):
            try: thread[name] = (task / name).read_text()
            except OSError as error: thread[name] = repr(error)
        evidence['threads'][task.name] = thread
    if shutil.which('gdb'):
        with (target / 'stalled-open-gdb.log').open('x') as out:
            try:
                result = subprocess.run(['gdb', '-batch', '-p', str(args.pid), '-ex',
                                         'thread apply all bt', '-ex', 'detach'],
                                        stdout=out, stderr=subprocess.STDOUT, timeout=20)
                evidence['gdb_returncode'] = result.returncode
            except subprocess.TimeoutExpired: evidence['gdb_timeout'] = True
    receipt = target / 'stalled-open.json'
    with receipt.open('x') as out:
        json.dump(evidence, out, ensure_ascii=False, indent=2); out.flush(); os.fsync(out.fileno())
    assert (proc / 'cmdline').read_bytes().split(b'\0')[0].decode() == expected
    os.kill(args.pid, signal.SIGTERM)
    print('SIGTERM requested for verified failed DB-open process', args.pid)
