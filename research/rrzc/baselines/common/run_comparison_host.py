"""Build shared clients, then run independent related-work arms serially."""
import argparse
import getpass
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import run_native_host as native
from comparison_source import METHODS, ARMS, make_payload, client_files
from export_source_host import (CalibrationRun, PARENT, IMAGE_DIR, configure, compact, digest,
    require, preflight, check_baseline_budget, detach_supervisor)
from result_storage import atomic_json, closed_archive
from run_pool_dbbench_host import BINARY
from run_joint_validation_host import foreign_supervisors
from analyze_comparison import analyze_arm, compare
from comparison_failure import failure_receipt

BUILD_DEFAULT = IMAGE_DIR / 'joint-related-compare-build-v2-20261109-results'


def wait_slot(output):
    end = time.monotonic() + 3600
    while True:
        try:
            require(not foreign_supervisors(), 'another experiment supervisor is active')
            budget = preflight(); check_baseline_budget(8 << 30)
            require(budget['data可用字节'] >= 42 << 30 and budget['可用内存字节'] >= 30 << 30, 'keep original resource floor')
            return
        except (ValueError, RuntimeError) as e:
            if not any(t in str(e) for t in ('another experiment', '已有本项目客机', '8101', '实验结果正在整理')): raise
            atomic_json(output / 'waiting.json', dict(reason=str(e), deadline_seconds=3600))
            require(time.monotonic() < end, 'shared guest slot deadline')
            time.sleep(20)


def record(output, **values):
    path = output / 'comparison-state.json'
    state = json.loads(path.read_text()) if path.exists() else dict(completed=[])
    state.update(values); atomic_json(path, state)
    print(json.dumps(values, ensure_ascii=False), flush=True)


def build_all(args, output, password):
    for method in METHODS:
        wait_slot(output)
        folder = output / method; folder.mkdir()
        payload = folder / 'payload.tar.gz'
        make_payload(method, payload)
        label = args.label + '-' + method.replace('_', '-')
        record(output, current=method, stage='building', child_output=str(folder))
        native.execute(SimpleNamespace(label=label, method=method), password, folder, payload)
        receipt = json.loads((folder / label / 'comparison_build.json').read_text())
        require(receipt['passed'] and receipt['posix_passed'], 'shared client build/POSIX failed')
        record(output, completed=[*json.loads((output / 'comparison-state.json').read_text())['completed'], method])
    record(output, stage='complete', current=None)


def admit_build(build, method):
    directory = build / method
    state, archive = closed_archive(directory)
    require(json.loads((directory / '阶段归档.json').read_text())['业务成功'], 'failed shared-client build')
    folder = directory / state['标签']
    proof = json.loads((folder / 'comparison_build.json').read_text())
    require(proof['passed'] and proof['method'] == method and proof['posix_passed'] and
            proof['original_library_unchanged'], 'incomplete build receipt')
    expected = {n: __import__('hashlib').sha256(b).hexdigest() for n, b in client_files().items()}
    require(expected == proof['client_files'], 'shared workload source changed since build')
    require(digest(folder / 'comparison_db_bench') == proof['binary_sha256'], 'binary changed')
    wanted = {'comparison_db_bench', 'comparison_build.json', 'comparison-client-identity.json', 'overlay_identity.json'}
    verified = set()
    with tarfile.open(archive, 'r|gz') as tar:
        for member in tar:
            prefix = state['标签'] + '/'
            if not member.name.startswith(prefix) or not member.isfile(): continue
            name = member.name[len(prefix):]
            if name not in wanted: continue
            h = __import__('hashlib').sha256()
            with tar.extractfile(member) as stream:
                for b in iter(lambda: stream.read(1 << 20), b''): h.update(b)
            require(h.hexdigest() == digest(folder / name), 'archive/local build mismatch: ' + name)
            verified.add(name)
    require(verified == wanted, 'missing archived shared build evidence')
    return folder, proof


def device_payload(output, build, arm, method, variant, windows):
    folder, proof = admit_build(build, method)
    path = output / 'payload.tar.gz'
    with tarfile.open(path, 'x:gz') as tar:
        for name in ('comparison_db_bench', 'comparison_build.json', 'overlay_identity.json'):
            tar.add(folder / name, arcname=name, recursive=False)
        sources = {'comparison_guest.py': HERE / 'comparison_guest.py',
                   'calibration_guest.py': HERE.parents[1] / 'tests/calibration_guest.py',
                   'strict_stats_guest.py': HERE.parents[1] / 'tests/strict_stats_guest.py'}
        for name, source in sources.items(): tar.add(source, arcname=name, recursive=False)
        data = json.dumps(dict(arm=arm, method=method, variant=variant, windows=windows)).encode()
        item = tarfile.TarInfo('comparison_plan.json'); item.size = len(data)
        tar.addfile(item, io.BytesIO(data))
    return path, proof


def run_arm(args, output, password, arm, method, variant, reference_root=None):
    wait_slot(output)
    folder = output / arm; folder.mkdir()
    payload, proof = device_payload(folder, args.build, arm, method, variant, args.windows)
    label = args.label + '-' + arm
    run = CalibrationRun(SimpleNamespace(label=label), password, folder, 'calibration', 8101, PARENT, retain_image=False)
    run.fresh_image = True; run.ftl_audit_binary = str(BINARY); run.ftl_audit_sha256 = digest(BINARY)
    configure(run, ftl_audit=False)
    run.archive_exclusions += ('payload.tar.gz',)
    policy = run.state['结果保存策略']
    policy.update(本轮归档含输入字节=False, 输入说明='共同冻结RandomGenerator与逐条Key规则确定性生成；保留生成输入CRC32C；无NAND快照',
                  共同客户端源码SHA256=proof['client_files'])
    run.record(结果保存策略=policy, GC启用=True, 方法=method, 变体=variant, 当前臂=arm,
               边界='共同负载有限前缀准入' if args.windows < 12 else '共同负载单轮完整输入，非统计重复')
    atomic_json(folder / '结果保存策略.json', policy)
    record(output, stage='running', current=arm, child_output=str(folder))
    error = None
    try:
        run.start(); run.ssh('test ! -e ' + run.target + ' && mkdir ' + run.target); run.deployed = True
        run.command(run.scp() + [str(payload), 'femu@127.0.0.1:' + run.target + '/payload.tar.gz'])
        require(run.ssh('sha256sum ' + run.target + '/payload.tar.gz').split()[0] == digest(payload), 'deployment changed')
        run.ssh('tar -xzf ' + run.target + '/payload.tar.gz -C ' + run.target)
        run.root('python3 ' + shlex.quote(run.target + '/comparison_guest.py') + ' ' + shlex.quote(run.target) +
                 ' > ' + shlex.quote(run.target + '/comparison-wrapper.log') + ' 2>&1', 7200)
    except BaseException as e:
        error = e; run.record(原始错误=repr(e)); raise
    finally:
        run.finish(error)
        saved = run.state.get('镜像处理', {})
        if saved.get('archive') and digest(Path(saved['archive'])) == saved['archive_sha256']:
            payload.unlink()
            compact(folder)
    report = analyze_arm(folder / label, args.windows)
    atomic_json(folder / 'comparison-audit.json', report)
    if arm != 'g2':
        first = (reference_root or output) / 'g2'
        initial = json.loads((first / '状态.json').read_text())['标签']
        atomic_json(folder / 'cross-arm-audit.json', compare(first / initial, folder / label,
                    json.loads((first / 'comparison-audit.json').read_text()), report))
    run.record(阶段='共同负载请求、内容摘要和逐阶段维护账目通过，已退出')
    record(output, completed=[*json.loads((output / 'comparison-state.json').read_text())['completed'], arm])


def reuse_archived_arm(args, output, arm, method, variant):
    parent = output / arm
    state, _ = closed_archive(parent)
    require(json.loads((parent / '阶段归档.json').read_text())['业务成功'], 'cannot resume failed device execution')
    folder = parent / state['标签']
    wanted = dict(arm=arm, method=method, variant=variant, windows=args.windows)
    require(json.loads((folder / 'comparison_plan.json').read_text()) == wanted, 'resumed device protocol differs')
    _, build = admit_build(args.build, method)
    require(digest(folder / 'comparison_db_bench') == build['binary_sha256'], 'resumed program differs')
    report = analyze_arm(folder, args.windows)
    receipt = parent / 'comparison-audit.json'
    if receipt.exists(): require(json.loads(receipt.read_text()) == json.loads(json.dumps(report)), 'existing audit differs')
    else: atomic_json(receipt, report)
    if arm != 'g2':
        first = output / 'g2'; first_state = json.loads((first / '状态.json').read_text())
        proof = compare(first / first_state['标签'], folder,
                        json.loads((first / 'comparison-audit.json').read_text()), report)
        cross = parent / 'cross-arm-audit.json'
        if cross.exists(): require(json.loads(cross.read_text()) == proof, 'existing cross-arm audit differs')
        else: atomic_json(cross, proof)
    record(output, completed=[*json.loads((output / 'comparison-state.json').read_text())['completed'], arm],
           reused_archived_arm=arm)


def retain_failed_arm(args, output, monitor, arm, method, variant):
    proof = failure_receipt(output / arm,
        dict(arm=arm, method=method, variant=variant, windows=args.windows))
    _, build = admit_build(args.build, method)
    require(proof['binary_sha256'] == build['binary_sha256'], 'failed arm used another program')
    atomic_json(monitor / (arm + '-failure-audit.json'), proof)
    state = json.loads((output / 'comparison-state.json').read_text())
    record(output, stage='continuing_after_archived_failure', failed_arms=[*state.get('failed_arms', []), arm],
           failed_evidence=str(monitor / (arm + '-failure-audit.json')), all_arms_passed=False)


def execute_arms(args, output, monitor, password):
    record(output, completed=[], failed_arms=[], stage='resuming' if args.resume else 'starting', error=None)
    for arm, method, variant in ARMS:
        if args.resume and (output / arm).exists():
            if args.continue_after_failed_arm and not json.loads(
                    (output / arm / '阶段归档.json').read_text())['业务成功']:
                retain_failed_arm(args, output, monitor, arm, method, variant)
            else:
                reuse_archived_arm(args, output, arm, method, variant)
        else:
            try:
                run_arm(args, output, password, arm, method, variant)
            except Exception:
                if not args.continue_after_failed_arm:
                    raise
                # Unclosed guests, deployment failures and post-processing errors
                # cannot pass failure_receipt; those still stop the controller.
                retain_failed_arm(args, output, monitor, arm, method, variant)
    failures = json.loads((output / 'comparison-state.json').read_text()).get('failed_arms', [])
    record(output, stage='complete_with_failures' if failures else 'complete', current=None,
           all_arms_passed=not failures)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', choices=('build', 'prefix', 'full'), required=True)
    p.add_argument('--label', required=True); p.add_argument('--build', type=Path, default=BUILD_DEFAULT)
    p.add_argument('--resume', type=Path)
    p.add_argument('--continue-after-failed-arm', action='store_true',
                   help='retain hash-verified closed business failures and attempt independent remaining arms; never mark all passed')
    p.add_argument('--detach', action='store_true'); p.add_argument('--check', action='store_true')
    args = p.parse_args(); args.windows = 12 if args.stage == 'full' else 1
    require(not args.continue_after_failed_arm or (args.stage == 'full' and args.resume),
            'failure continuation is explicit full-run recovery only')
    require(args.label.startswith('joint-related-compare-') and
            all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.label), 'invalid comparison label')
    output = IMAGE_DIR / (args.label + '-results')
    require(not output.exists() or args.resume, 'refuse overwrite')
    if args.resume:
        require(args.stage in ('prefix', 'full') and args.resume.resolve() == output.resolve() and
                json.loads((output / 'comparison-state.json').read_text())['stage'] == 'failed',
                'resume only an archived stopped run with matching identity')
    if args.check:
        print(json.dumps(dict(stage=args.stage, windows=args.windows, arms=ARMS, resources=preflight()), ensure_ascii=False)); return
    if args.stage != 'build':
        build_state = json.loads((args.build / 'comparison-state.json').read_text())
        require(build_state['stage'] == 'complete' and set(build_state['completed']) == set(METHODS),
                'complete all three common-client builds before device admission')
        proofs = [admit_build(args.build, method)[1] for method in METHODS]
        require(all(p['client_files'] == proofs[0]['client_files'] for p in proofs), 'cross-build client mismatch')
    if args.stage == 'full':
        prefix = IMAGE_DIR / args.label.replace('-full-', '-prefix-')
        prefix = Path(str(prefix) + '-results')
        require((prefix / 'comparison-state.json').is_file() and
                json.loads((prefix / 'comparison-state.json').read_text())['stage'] == 'complete',
                'first complete the matching six-arm prefix')
        from verify_comparison_host import verify
        with tempfile.TemporaryDirectory(prefix='rrzc-full-admission-') as temp:
            receipt = Path(temp) / 'prefix-admission.json'
            verify(prefix, receipt)
            prefix_proof = json.loads(receipt.read_text())
        require(prefix_proof['passed'] and prefix_proof['plan']['windows'] == 1 and
                Path(prefix_proof['plan']['build']).resolve() == args.build.resolve(),
                'prefix verification/build differs from full run')
    password = getpass.getpass('共同负载串行实验认证（仅内存）：')
    if args.resume:
        index = 1
        while (output / ('resume-v' + str(index))).exists(): index += 1
        monitor = output / ('resume-v' + str(index))
        monitor.mkdir()
        shutil.copyfile(output / 'comparison-state.json', monitor / 'previous-state.json')
        freeze = monitor / 'entry-source'; freeze.mkdir()
    else:
        output.mkdir(); monitor = output
        freeze = output / 'entry-source'; freeze.mkdir()
    for source in [Path(__file__), HERE / 'comparison_source.py', HERE / 'comparison_guest.py', HERE / 'analyze_comparison.py']:
        shutil.copyfile(source, freeze / source.name)
    atomic_json(freeze / 'SHA256.json', {p.name: digest(p) for p in freeze.iterdir()})
    registered = dict(stage=args.stage, windows=args.windows,
        arms=ARMS, requests=args.windows * 188416, load_keys=1048576, value_bytes=4096,
        independent_empty_devices=True, concurrent_guests=1, original_RR_threshold=1024,
        GC_thresholds=dict(g2=20, caza=20, oaza_gc=15, coldmap=20),
        extra_greedy_repeats=False, formal_performance_claim=False,
        input_bytes_archived=False, logical_input='frozen verified workload generator; CRC32C is not bytewise evidence',
        build=str(args.build))
    if args.resume:
        require(json.loads((output / 'comparison-plan.json').read_text()) == json.loads(json.dumps(registered)),
                'resumed plan differs')
    else: atomic_json(output / 'comparison-plan.json', registered)
    if args.stage == 'full':
        if args.resume: atomic_json(monitor / 'prefix-admission.json', prefix_proof)
        else: atomic_json(output / 'prefix-admission.json', prefix_proof)
    atomic_json(monitor / 'recovery-policy.json', dict(
        continue_after_failed_arm=args.continue_after_failed_arm,
        full_workload_requires_every_arm_passed=True, repeat_successful_arms=False))
    shutil.copyfile(HERE / 'comparison_failure.py', freeze / 'comparison_failure.py')
    atomic_json(freeze / 'SHA256-with-failure-audit.json', {p.name: digest(p) for p in freeze.iterdir()
                if p.suffix == '.py'})
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(monitor): return
    try:
        record(output, supervisor_pid=os.getpid(), supervisor_log=str(monitor / '独立监督.log'))
        if args.stage == 'build': build_all(args, output, password)
        else:
            execute_arms(args, output, monitor, password)
    except BaseException as e:
        record(output, stage='failed', error=repr(e)); raise
    finally: password = None


if __name__ == '__main__': main()
