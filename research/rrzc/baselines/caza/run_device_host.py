"""Reuse a verified CAZA binary in one fresh temporary FEMU guest; archive then reclaim."""
import argparse
import getpass
import io
import json
from pathlib import Path
import signal
import sys
import tarfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from export_source_host import (CalibrationRun, PARENT, IMAGE_DIR, configure, compact,
                                digest, require, preflight, check_baseline_budget,
                                detach_supervisor)
from result_storage import closed_archive, atomic_json
from run_pool_dbbench_host import BINARY
from analyze_placement_integration import analyze as audit


def prepare(source, payload, test_case='basic'):
    require(test_case in ('basic', 'direct', 'rr', 'pressure', 'gc', 'fault', 'nospace'), 'invalid test case')
    state, archive = closed_archive(source)
    old = source / state['标签']
    names = ('caza_probe', 'build_complete.json', 'overlay_identity.json',
             'core_abi_check.json', 'posix-smoke.log', 'build.log')
    if test_case == 'direct': names += ('posix-direct-smoke.log',)
    if test_case in ('pressure', 'gc', 'nospace'): names += ('posix-pressure-smoke.log',)
    if test_case == 'fault': names += ('fault_io.so', 'fault-scope.log')
    test_sources = ('probe.cc', 'rr_probe.h', 'pressure_probe.h', 'nospace_probe.h', 'fault_io.cc')
    if test_case != 'basic': names += test_sources
    expected = {name: digest(old / name) for name in names}
    found = set()
    import hashlib
    with tarfile.open(archive) as tar:
        for member in tar:
            prefix = state['标签'] + '/'
            if member.name.startswith(prefix) and member.name[len(prefix):] in expected:
                name = member.name[len(prefix):]
                require(member.isfile() and name not in found, 'invalid/duplicate admitted archive member')
                require(hashlib.sha256(tar.extractfile(member).read()).hexdigest() == expected[name],
                        'extracted admission evidence differs from verified archive: ' + name)
                found.add(name)
    require(found == set(expected), 'admission archive incomplete')
    proof = json.loads((old / 'build_complete.json').read_text())
    require(proof['passed'] and proof['NDEBUG'] and proof['known_failing_member_offsets_match'],
            'build admission failed')
    if test_case != 'basic':
        require(all(digest(old / n) == digest(HERE / n) for n in test_sources),
                'binary test-source identity does not match requested case')
    if test_case not in proof.get('supported_cases', ['basic']):
        # Early build receipts lack a capability list. Admit only an exactly
        # matching archived test implementation; never infer support from a log name.
        require(test_case != 'basic' and all(digest(old / n) == digest(HERE / n)
                for n in test_sources), 'binary test-source identity does not match requested case')
    require(json.loads((source / '阶段归档.json').read_text())['业务成功'] and
            'CAZA posix latest values/checksums/reopen passed' in (old / 'posix-smoke.log').read_text() and
            'posix-smoke passed' in (old / 'build.log').read_text(), 'POSIX admission failed')
    if test_case == 'direct':
        require('CAZA posix latest values/checksums/reopen passed' in (old / 'posix-direct-smoke.log').read_text() and
                'posix-direct-smoke passed' in (old / 'build.log').read_text(), 'direct POSIX admission failed')
    if test_case in ('pressure', 'gc', 'nospace'):
        require('CAZA posix latest values/checksums/reopen passed' in (old / 'posix-pressure-smoke.log').read_text() and
                'posix-pressure-smoke passed' in (old / 'build.log').read_text(), 'pressure POSIX admission failed')
    if test_case == 'fault':
        require(digest(old / 'fault_io.so') == proof['fault_library_sha256'] and
                'regular files preserved' in (old / 'fault-scope.log').read_text(), 'fault-library admission failed')
    require(digest(old / 'caza_probe') == proof['binary_sha256'], 'binary identity mismatch')
    with tarfile.open(payload, 'x:gz') as tar:
        for name in ('caza_probe', 'build_complete.json', 'overlay_identity.json'):
            tar.add(old / name, arcname=name, recursive=False)
        if test_case == 'fault': tar.add(old / 'fault_io.so', arcname='fault_io.so', recursive=False)
        data = json.dumps(dict(passed=True, source_log_sha256=digest(old / 'posix-smoke.log'),
                               build_log_sha256=digest(old / 'build.log'))).encode()
        item = tarfile.TarInfo('posix_complete.json'); item.size = len(data)
        tar.addfile(item, io.BytesIO(data))
        tar.add(HERE / 'device_guest.py', arcname='device_guest.py')
        data = json.dumps(dict(case=test_case)).encode()
        item = tarfile.TarInfo('test_case.json'); item.size = len(data)
        tar.addfile(item, io.BytesIO(data))
    return dict(source=str(source), archive_sha256=digest(archive), binary_sha256=proof['binary_sha256'],
                verified_archive_files=expected)


def execute(args, password, output, payload):
    run = CalibrationRun(args, password, output, 'device', 8101, PARENT, retain_image=False)
    run.fresh_image = True
    run.ftl_audit_binary = str(BINARY); run.ftl_audit_sha256 = digest(BINARY)
    configure(run, ftl_audit=False)
    run.record(边界='CAZA真实空盘放置及重挂载小测，不是收益实验', GC启用=args.case == 'gc',测试用例=args.case)
    policy = run.state['结果保存策略']
    policy.update(本轮归档含输入字节=False, 输入说明='归档程序生成固定键和值；GC另含非SST填充清单；非完整NAND检查点')
    atomic_json(output / '结果保存策略.json', policy)
    run.archive_exclusions += ('payload.tar.gz',)
    error = None
    try:
        run.start(); run.ssh('test ! -e ' + run.target + ' && mkdir ' + run.target)
        run.deployed = True
        run.command(run.scp() + [str(payload), 'femu@127.0.0.1:' + run.target + '/payload.tar.gz'])
        require(run.ssh('sha256sum ' + run.target + '/payload.tar.gz').split()[0] == digest(payload),
                'payload identity changed')
        run.ssh('tar -xzf ' + run.target + '/payload.tar.gz -C ' + run.target)
        run.record(阶段='核验全空FEMU设备，执行CAZA放置与完整内容/重新挂载小测')
        run.root('python3 ' + run.target + '/device_guest.py ' + run.target +
                 ' > ' + run.target + '/device-wrapper.log 2>&1', 900 if args.case in ('gc', 'nospace') else 420)
        run.record(阶段='CAZA设备小测完成，归档并正常关机')
    except BaseException as failure:
        error = failure; run.record(原始错误=repr(failure)); raise
    finally:
        run.finish(error)
        saved = run.state.get('镜像处理', {})
        if saved.get('archive') and digest(Path(saved['archive'])) == saved.get('archive_sha256'):
            payload.unlink()
        compact(output)
    local = output / args.label
    require(json.loads((local / 'device_complete.json').read_text())['passed'], 'device result incomplete')
    for mode in ('device', 'verify'):
        report = audit(local / (mode + '-composition.bin'))
        atomic_json(output / (mode + '-composition-audit.json'), report)
    run.record(阶段='真实放置和重挂载通过，组成记录已解析，客机已退出')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build', type=Path, required=True)
    p.add_argument('--label', required=True)
    p.add_argument('--case', choices=('basic', 'direct', 'rr', 'pressure', 'gc', 'fault', 'nospace'), default='basic')
    p.add_argument('--check', action='store_true'); p.add_argument('--detach', action='store_true')
    args = p.parse_args()
    require(args.label.startswith('joint-related-caza-device-') and
            all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.label), 'invalid label')
    budget = preflight()
    budget.update(目录已占字节=check_baseline_budget(1 << 30), 新增磁盘预算字节=1 << 30,
                  总内存预算字节=12 << 30, 并发客机数=1)
    output = IMAGE_DIR / (args.label + '-results'); require(not output.exists(), 'refuse overwrite')
    if args.check:
        print(json.dumps(budget, ensure_ascii=False, indent=2)); return
    password = getpass.getpass('独立设备小测认证（仅内存）：')
    output.mkdir(); payload = output / 'payload.tar.gz'
    source = prepare(args.build.resolve(), payload, args.case)
    atomic_json(output / '来源及计划.json', dict(source=source, resources=budget,
        payload_sha256=digest(payload), ftl_audit=False, test_case=args.case,
        scope='fresh isolated FEMU only; no original filesystem modification'))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output): return
    try: execute(args, password, output, payload)
    finally: password = None


if __name__ == '__main__': main()
