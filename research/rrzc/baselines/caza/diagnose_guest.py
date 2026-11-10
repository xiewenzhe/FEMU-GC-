"""Read-only archive/symbol inspection and isolated reproduction under GDB."""
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys


def main(target):
    proof = json.loads((target / 'diagnostic_identity.json').read_text())
    binary = target / 'caza_probe'
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == proof['binary_sha256']
    root = Path('/home/femu/rocksdb')
    library = root / 'librocksdb_debug.a'
    before = hashlib.sha256(library.read_bytes()).hexdigest()
    assert before == proof['original_library_sha256']
    env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', ZENFS_RELATED_WORK='caza')
    commands = [(['gdb', '--batch', '-ex', 'set disable-randomization off',
                  '-ex', 'run', '-ex', 'thread apply all bt', '--args',
                  str(binary), 'posix', str(target)], 'gdb'),
                (['ar', 't', str(library)], 'archive-members')]
    if not shutil.which('gdb'):
        # No package installation: a diagnostic-only preload captures the signal
        # stack, without changing the original executable or any library bytes.
        code = r'''#define _GNU_SOURCE
#include <execinfo.h>
#include <signal.h>
#include <unistd.h>
static void fault(int sig) {
  void* frames[64]; int count=backtrace(frames,64);
  backtrace_symbols_fd(frames,count,2); _exit(128+sig);
}
__attribute__((constructor)) static void install(void) {
  struct sigaction action={0}; action.sa_handler=fault;
  sigemptyset(&action.sa_mask); sigaction(SIGSEGV,&action,0); sigaction(SIGABRT,&action,0);
}
'''
        (target / 'fault_trace.c').write_text(code)
        subprocess.run(['gcc', '-shared', '-fPIC', '-g', str(target / 'fault_trace.c'),
                        '-o', str(target / 'fault_trace.so')], check=True)
        env['LD_PRELOAD'] = str(target / 'fault_trace.so')
        commands[0] = ([str(binary), 'posix', str(target)], 'signal-stack')
    for argv, name in commands:
        (target / (name + '.command.json')).write_text(json.dumps(argv) + '\n')
        with (target / (name + '.log')).open('x') as log:
            code = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT,
                                  env=env, cwd=root, timeout=120).returncode
        (target / (name + '.exit')).write_text(str(code) + '\n')
    members = (target / 'archive-members.log').read_text().splitlines()
    for name in ('db_impl_compaction_flush.o', 'db_impl_open.o', 'flush_job.o', 'db_impl.o'):
        assert name in members, name
        obj = target / ('original-' + name)
        with obj.open('xb') as out:
            subprocess.run(['ar', 'p', str(library), name], stdout=out, check=True)
        for argv, suffix in ((['nm', '-C', str(obj)], 'symbols'),
                             (['objdump', '-drC', str(obj)], 'disassembly')):
            with (target / (name + '.' + suffix + '.log')).open('x') as out:
                subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT, check=True)
    assert hashlib.sha256(library.read_bytes()).hexdigest() == before
    (target / 'diagnostic_complete.json').write_text(json.dumps(dict(
        evidence_captured=True, original_library_unchanged=True,
        functional_test_passed=False, performance_experiment=False), indent=2) + '\n')


if __name__ == '__main__': main(Path(sys.argv[1]).resolve())
