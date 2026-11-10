"""Wait for the shared guest slot, then run the reviewed joint checks serially."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path[:0] = [str(HERE), str(BASE)]
from export_source_host import IMAGE_DIR, preflight, detach_supervisor, check_baseline_budget
from result_storage import atomic_json, closed_archive

AUTH_BOOTSTRAP = ("import getpass,runpy,sys; p=sys.stdin.readline().rstrip(chr(10)); "
                  "getpass.getpass=lambda prompt='':p; sys.argv=sys.argv[1:]; "
                  "runpy.run_path(sys.argv[0],run_name='__main__')")


def foreign_supervisors():
    """Wait across the other pair's between-arm gap as well as its guest time."""
    active = []
    for process in Path('/proc').iterdir():
        if not process.name.isdigit(): continue
        try:
            if not (process / 'comm').read_text().startswith('python'): continue
            args = (process / 'cmdline').read_bytes().decode(errors='replace').split('\0')
            scripts = [a for a in args if a.endswith('_host.py') and
                       (a.startswith('research/rrzc/tests/run_') or '/research/rrzc/tests/run_' in a)]
            if scripts: active.append(dict(pid=int(process.name), scripts=scripts))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return active


def child_active(pid, label):
    process = Path('/proc') / str(pid)
    try:
        state = (process / 'stat').read_text().split(') ', 1)[1][0]
        return state != 'Z' and label.encode() in (process / 'cmdline').read_bytes()
    except FileNotFoundError:
        return False


def job_complete(result, kind):
    stage, status = result / '阶段归档.json', result / '状态.json'
    if not stage.exists() or not status.exists(): return False
    saved = json.loads(status.read_text())
    if child_active(saved['监督进程PID'], saved['标签']): return False
    closed_archive(result)
    if not json.loads(stage.read_text())['业务成功']:
        raise RuntimeError('device/build failed: ' + saved['标签'])
    if kind == 'device':
        if saved['阶段'] != '归档哈希、退出和逐笔事件账目通过':
            raise RuntimeError('post-archive audit did not complete: ' + saved['标签'])
        for name in ('device-composition-audit.json', 'verify-composition-audit.json', 'physical-mapping-audit.json'):
            if not (result / name).is_file(): raise RuntimeError('missing post-archive evidence: ' + name)
        if not json.loads((result / 'physical-mapping-audit.json').read_text())['passed']:
            raise RuntimeError('physical mapping audit failed')
    return True


def plan():
    caza = 'joint-related-caza-build-joint-v2-20261109'
    oaza = 'joint-related-oaza-gc-build-v10-20261109'
    cold = 'joint-related-coldmap-build-v6-20261109'
    return [
        dict(kind='build', method='caza', label=caza, overlay='caza-v6-20261107'),
        dict(kind='device', method='caza', label='joint-related-caza-device-joint-v1-20261109',
             build=caza, variant='native', case='joint'),
        dict(kind='build', method='oaza_gc', label=oaza, overlay='oaza-gc-v7-20261109'),
        dict(kind='device', method='oaza_gc', label='joint-related-oaza-gc-device-recovery-literal-v2-20261109',
             build=oaza, variant='literal', case='recovery'),
        dict(kind='device', method='oaza_gc', label='joint-related-oaza-gc-device-recovery-adapted-v1-20261109',
             build=oaza, variant='adapted', case='recovery'),
        dict(kind='device', method='coldmap', label='joint-related-coldmap-device-joint-printed-v1-20261109',
             build=cold, variant='printed', case='joint')]


def execute(output, password, max_wait):
    jobs = plan(); deadline = time.monotonic() + max_wait
    # Authentication travels only through an anonymous pipe. The existing child
    # supervisor retains it in memory after detach; no argv/env/password file.
    state = dict(status='waiting', supervisor_pid=os.getpid(), jobs=jobs, completed=[], total_memory_budget_GiB=12,
                 concurrent_guests=1, authentication='memory/anonymous pipe only')
    def record(**values):
        state.update(values); atomic_json(output / 'queue.json', state)
        print(json.dumps(values, ensure_ascii=False), flush=True)
    try:
        for job in jobs:
            result = IMAGE_DIR / (job['label'] + '-results')
            if result.exists():
                if not job_complete(result, job['kind']):
                    raise ValueError('existing output has not passed: ' + str(result))
                state['completed'].append(job['label'])
                record(status='reused_verified_job', current=job)
                continue
            script = HERE / ('run_native_host.py' if job['kind'] == 'build' else 'run_device_host.py')
            argv = ['--method', job['method'], '--label', job['label'], '--detach']
            if job['kind'] == 'build': argv += ['--overlay', str(BASE / 'workspaces' / job['overlay'])]
            else:
                argv += ['--build', str(IMAGE_DIR / (job['build'] + '-results')),
                         '--variant', job['variant'], '--case', job['case']]
            attempt = 0
            while True:
                if time.monotonic() >= deadline: raise TimeoutError('shared guest slot wait deadline')
                foreign = foreign_supervisors()
                if foreign:
                    record(status='waiting', current=job, reason='other experiment supervisor still active',
                           other_supervisors=foreign)
                    time.sleep(20); continue
                try:
                    preflight()
                    check_baseline_budget((2 if job['kind'] == 'build' else 1) << 30)
                except ValueError as error:
                    if not any(reason in str(error) for reason in ('已有本项目客机', '8101', '实验结果正在整理')): raise
                    record(status='waiting', current=job, reason=str(error))
                    time.sleep(20); continue
                attempt += 1
                record(status='launching', current=job, reason=None, other_supervisors=[])
                log_path = output / (job['label'] + '-attempt-' + str(attempt) + '.log')
                with log_path.open('x') as log:
                    launch = subprocess.run([sys.executable, '-c', AUTH_BOOTSTRAP, str(script), *argv],
                        input=password + '\n', text=True, stdout=log, stderr=subprocess.STDOUT, timeout=120)
                if not launch.returncode: break
                transient = any(reason in log_path.read_text() for reason in
                                ('实验结果正在整理', '已有本项目客机', '8101端口'))
                if transient and not result.exists():
                    record(status='waiting', reason='child preflight resource race; no output or guest created')
                    time.sleep(20); continue
                raise RuntimeError('job launch failed: ' + job['label'])
            record(status='running', current=job, result_directory=str(result))
            job_deadline = time.monotonic() + 2400
            while True:
                # Stage receipts precede post-processing. Require both the
                # original supervisor exit and the post-archive evidence.
                if job_complete(result, job['kind']): break
                if time.monotonic() >= job_deadline: raise TimeoutError('child supervisor timeout: ' + job['label'])
                time.sleep(5)
            state['completed'].append(job['label']); record(status='completed_job')
        record(status='complete', current=None)
    except BaseException as error:
        record(status='failed', error=repr(error)); raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--max-wait', type=int, default=10800)
    args = parser.parse_args(); output = args.output.resolve()
    if output.exists(): raise ValueError('refuse queue overwrite')
    password = getpass.getpass('联合验证队列认证（仅内存）：')
    output.mkdir(parents=True)
    (output / 'queue_source.py').write_bytes(Path(__file__).read_bytes())
    if args.detach and not detach_supervisor(output): return
    try: execute(output, password, args.max_wait)
    finally: password = None


if __name__ == '__main__': main()
