"""Local startup adapter: preserve VM/lifecycle checks, diagnose SSH separately."""
import hashlib
import inspect
from pathlib import Path
import subprocess
import textwrap
import time

import pexpect
from run_comparison_host import CalibrationRun, atomic_json
import run_tail_ablation_fixed_pair as fixed
import run_dbbench_parallel_host as database


def control_path(original):
    # OpenSSH appends a dot and 16 random characters before renaming its socket.
    # Linux sockaddr_un.sun_path needs a trailing NUL as well (108 bytes total).
    if len(original.encode()) + 17 < 108: return original
    return '/tmp/rrzc-' + hashlib.sha256(original.encode()).hexdigest()[:24] + '.sock'


def authenticate(run):
    original = run.socket
    run.socket = control_path(original)
    assert not Path(run.socket).exists(), 'SSH control socket already exists'
    run.record(SSH控制路径=run.socket, SSH原控制路径=original)
    receipt = dict(original_control_path=original, control_path=run.socket, attempts=[],
                   boundary='no credential or terminal transcript retained; master and remote identity checked separately')
    path = run.output / '启动认证核验.json'
    def save(**values):
        receipt.update(values); atomic_json(path, receipt)
    argv = ['-M', '-N', '-f', '-o', 'ControlPath=' + run.socket, '-o', 'ControlPersist=3600',
            '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=[127.0.0.1]:8089',
            '-o', 'ConnectTimeout=8', '-o', 'NumberOfPasswordPrompts=1',
            '-p', run.port, 'femu@127.0.0.1']
    for attempt in range(18):
        child = pexpect.spawn('ssh', argv, encoding='utf-8', timeout=15, echo=False)
        sent = False
        try:
            response = child.expect([r'[Pp]assword:', pexpect.EOF, pexpect.TIMEOUT])
            if response == 0:
                sent = True; child.sendline(run.password)
                response = child.expect([pexpect.EOF, pexpect.TIMEOUT])
            # Never persist arbitrary terminal output around a credential prompt.
            # Retain only fixed error categories, exit/signal codes and identity.
            text = child.before or ''
            categories = [word for word in ('Permission denied', 'Host key verification failed',
                'unix_listener', 'too long', 'Connection refused', 'Connection closed',
                'Connection reset', 'Connection timed out') if word in text]
            timed_out = child.isalive()
            child.close(force=timed_out)
            receipt['attempts'].append(dict(attempt=attempt + 1, credential_sent=sent,
                exit_status=child.exitstatus, signal_status=child.signalstatus,
                timed_out=timed_out, diagnostics=categories))
            save(stage='master_checked')
            if sent:
                assert not timed_out and child.exitstatus == 0, 'SSH master failed; see startup diagnostic categories'
                identity = subprocess.run(['ssh', '-S', run.socket, '-o', 'BatchMode=yes',
                    '-p', run.port, 'femu@127.0.0.1', 'id'], capture_output=True, text=True, timeout=20)
                # id has no credential input; its stdout can be retained directly.
                save(stage='identity_checked', identity_stdout=identity.stdout,
                     identity_exit=identity.returncode, passed=identity.returncode == 0 and
                     'uid=1000(femu)' in identity.stdout)
                assert receipt['passed'], 'SSH remote identity failed'
                legacy = getattr(run, 'diagnose_legacy_control_path', None)
                if legacy:
                    assert not Path(legacy).exists() and control_path(legacy) != legacy
                    probe_args = list(argv)
                    probe_args[probe_args.index('ControlPath=' + run.socket)] = 'ControlPath=' + legacy
                    probe = pexpect.spawn('ssh', probe_args, encoding='utf-8', timeout=20, echo=False)
                    try:
                        prompt = probe.expect([r'[Pp]assword:', pexpect.EOF, pexpect.TIMEOUT])
                        assert prompt == 0, 'legacy socket probe did not reach authentication'
                        probe.sendline(run.password)
                        probe.expect(pexpect.EOF)
                        output = probe.before or ''; probe.close()
                        diagnostics = [x for x in ('unix_listener', 'too long', 'Permission denied') if x in output]
                        confirmed = probe.exitstatus == 255 and diagnostics == ['unix_listener', 'too long']
                        save(legacy_socket_probe=dict(path=legacy, exit_status=probe.exitstatus,
                             diagnostics=diagnostics, reproduced=confirmed))
                        assert confirmed, 'legacy failure hypothesis not confirmed; preserve diagnostics'
                    finally:
                        if probe.isalive(): probe.close(force=True)
                return
            assert not any(x in categories for x in ('Permission denied', 'Host key verification failed')), \
                'SSH authentication or host key mismatch'
        finally:
            if child.isalive(): child.close(force=True)
        time.sleep(4)
    raise RuntimeError('new guest SSH readiness deadline')


def adapted_start():
    source = textwrap.dedent(inspect.getsource(fixed.FixedPair.start))
    anchor = '    argv = ['
    assert source.count(anchor) == 1
    source = source.split(anchor)[0] + '    authenticate(self)\n'
    namespace = dict(vars(fixed), authenticate=authenticate)
    exec(compile(source, str(Path(__file__).resolve()) + ':fixed-start', 'exec'), namespace)
    fixed_start = namespace['start']
    source = textwrap.dedent(inspect.getsource(database.DbRun.start))
    anchor = 'FixedPair.start(self, self.tag)'
    assert source.count(anchor) == 1
    namespace = dict(vars(database), fixed_start=fixed_start)
    exec(compile(source.replace(anchor, 'fixed_start(self, self.tag)'),
                 str(Path(__file__).resolve()) + ':lifecycle-start', 'exec'), namespace)
    return namespace['start']


class ReliableCalibrationRun(CalibrationRun):
    _fresh_start = adapted_start()

    def start(self):
        assert self.fresh_image, 'adapter only supports independently created temporary guests'
        return self._fresh_start()
