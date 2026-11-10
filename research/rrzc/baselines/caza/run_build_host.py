"""Detached CAZA compilation/functional probe in a temporary system-only guest."""
import argparse
import getpass
import json
from pathlib import Path
import signal
import sys
import tarfile

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(BASE))
from export_source_host import (SourceRun, PARENT, IMAGE_DIR, configure, compact,
                                digest, require, preflight, check_baseline_budget,
                                detach_supervisor)
from result_storage import closed_archive

COMMAND_ROOT = IMAGE_DIR / ('joint-controlled-end-to-end-v1-20261105-results/'
                          'joint-controlled-integrated-pool-v2-20261104')


def payload_for(overlay, payload, diagnose=None):
    if diagnose:
        state, archive_path = closed_archive(diagnose)
        source = diagnose / state['标签']
        proof = json.loads((source / 'build_complete.json').read_text())
        require(proof['passed'] and digest(source / 'caza_probe') == proof['binary_sha256'],
                'diagnostic binary identity mismatch')
        identity = dict(proof, archive_sha256=digest(archive_path))
        with tarfile.open(payload, 'x:gz') as archive:
            archive.add(source / 'caza_probe', arcname='caza_probe')
            archive.add(HERE / 'diagnose_guest.py', arcname='diagnose_guest.py')
            import io
            data = json.dumps(identity).encode()
            item = tarfile.TarInfo('diagnostic_identity.json'); item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        return
    identity = json.loads((overlay / 'overlay_identity.json').read_text())
    src = overlay / 'src/rocksdb'
    require(all(digest(src / n) == h for n, h in identity['source_sha256'].items()),
            'overlay source changed')
    with tarfile.open(payload, 'x:gz') as archive:
        archive.add(overlay / 'overlay_identity.json', arcname='overlay_identity.json')
        for name in identity['source_sha256']:
            archive.add(src / name, arcname='src/rocksdb/' + name, recursive=False)
        for name in ('build_guest.py', 'probe.cc', 'rr_probe.h', 'pressure_probe.h',
                     'nospace_probe.h', 'fault_io.cc', 'fault_scope_test.py'):
            archive.add(HERE / name, arcname=name)
        archive.add(COMMAND_ROOT / '计时编译-fs_zenfs.命令.json', arcname='compile.original.json')


def execute(args, password, output, payload):
    run = SourceRun(args, password, output, 'compile', 8101, PARENT, retain_image=False)
    run.system_memory_gib = 4
    run.fresh_image = True
    configure(run, ftl_audit=False)
    policy = run.state['结果保存策略']
    policy.update(本轮归档含输入字节=False, 输入说明='仅独立构建与POSIX功能校验，不接模拟SSD')
    run.record(结果保存策略=policy, 边界='CAZA八个受影响单元独立构建及POSIX小测，无性能实验')
    # Keep source identity, patch and binary; no redundant remote source/object tree.
    run.archive_exclusions += ('payload.tar.gz', 'src', 'objects', 'original-*.o')
    error = None
    try:
        run.start()
        run.ssh('test ! -e ' + run.target + ' && mkdir ' + run.target)
        run.deployed = True
        run.command(run.scp() + [str(payload), 'femu@127.0.0.1:' + run.target + '/payload.tar.gz'])
        require(run.ssh('sha256sum ' + run.target + '/payload.tar.gz').split()[0] == digest(payload),
                'transfer identity mismatch')
        run.ssh('tar -xzf ' + run.target + '/payload.tar.gz -C ' + run.target)
        run.record(阶段='串行编译八个受影响单元及CAZA客户端')
        # Stream output into a file; the detached supervisor owns the guest throughout.
        helper = 'diagnose_guest.py' if args.diagnose else 'build_guest.py'
        run.ssh('python3 ' + run.target + '/' + helper + ' ' + run.target +
                ' > ' + run.target + '/build.log 2>&1', 1800)
        run.record(阶段=('诊断证据已采集，不代表功能通过；采证后关机' if args.diagnose else
                        'CAZA编译及普通目录内容校验完成，采证后关机'))
    except BaseException as failure:
        error = failure
        run.record(原始错误=repr(failure))
        raise
    finally:
        run.finish(error)
        saved = run.state.get('镜像处理', {})
        if saved.get('archive') and digest(Path(saved['archive'])) == saved.get('archive_sha256'):
            payload.unlink()
        compact(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--overlay', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--diagnose', type=Path)
    args = parser.parse_args()
    require(args.label.startswith('joint-related-caza-build-') and
            all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.label), 'invalid label')
    budget = preflight()
    budget.update(目录已占字节=check_baseline_budget(2 << 30), 新增磁盘预算字节=2 << 30,
                  系统内存字节=4 << 30, 模拟SSD接入=False)
    output = IMAGE_DIR / (args.label + '-results')
    require(not output.exists(), 'do not overwrite build evidence')
    if args.check:
        print(json.dumps(budget, ensure_ascii=False, indent=2)); return
    password = getpass.getpass('独立构建客机认证（仅内存）：')
    output.mkdir()
    payload = output / 'payload.tar.gz'
    payload_for(args.overlay.resolve(), payload, args.diagnose)
    (output / '构建来源.json').write_text(json.dumps(dict(
        overlay=str(args.overlay.resolve()), identity=digest(args.overlay / 'overlay_identity.json'),
        payload_sha256=digest(payload), resources=budget,
        purpose='CAZA compilation and POSIX functional check', source_mutation=False,
        cleanup='ImageLease: verified archive, normal shutdown, reclaim temporary image'),
        ensure_ascii=False, indent=2) + '\n')
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not detach_supervisor(output): return
    try:
        execute(args, password, output, payload)
    finally:
        password = None


if __name__ == '__main__':
    main()
