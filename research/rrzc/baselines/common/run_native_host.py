"""Build a new isolated paper overlay with the existing archive/image lifecycle."""
import argparse
import getpass
import io
import json
from pathlib import Path
import signal
import sys
import tarfile

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(BASE))
from export_source_host import (SourceRun, PARENT, IMAGE_DIR, configure, compact, digest,
                                require, preflight, check_baseline_budget, detach_supervisor)

CAZA = BASE / 'caza'
COMMAND_ROOT = IMAGE_DIR / ('joint-controlled-end-to-end-v1-20261105-results/'
                           'joint-controlled-integrated-pool-v2-20261104')


def joint_header(method):
    data = (BASE / 'coldmap/joint_probe.h').read_bytes()
    if method == 'caza':
        text = data.decode().replace('ExerciseColdMapJoint', 'ExerciseCazaJoint').replace('ColdMap', 'CAZA')
        anchor = '  Check(db->VerifyChecksum()); DumpMapping('
        require(text.count(anchor) == 1, 'joint validation anchor changed')
        text = text.replace(anchor, '  if (!verify_only) ExerciseJointRR(*db, fs, device, output, logger);\n' + anchor)
        return text.encode()
    return data


def probe_source(method):
    text = (CAZA / 'probe.cc').read_text()
    if method == 'caza':
        text = text.replace('#include "nospace_probe.h"', '#include "nospace_probe.h"\n#include "joint_rr_probe.h"\n#include "caza_joint_probe.h"')
        text = text.replace('test_case == "fault", "unknown test case");', 'test_case == "fault" || test_case == "joint", "unknown test case");')
        text = text.replace('test_case != "nospace",', 'test_case != "nospace" && test_case != "joint",')
        text = text.replace('      if (test_case == "nospace")',
            '      if (test_case == "joint")\n'
            '        ExerciseCazaJoint(env.get(), *fs, *device, output, mode == "verify", logger.get());\n'
            '      else if (test_case == "nospace")')
    if method == 'coldmap':
        text = text.replace('#include "nospace_probe.h"', '#include "nospace_probe.h"\n#include "coldmap_model_probe.h"\n#include "coldmap_joint_probe.h"')
        text = text.replace('test_case == "fault", "unknown test case");',
            'test_case == "fault" || test_case == "model" || test_case == "joint", "unknown test case");')
        text = text.replace('test_case != "fault" && test_case != "nospace",',
            'test_case != "fault" && test_case != "nospace" && test_case != "model" && test_case != "joint",')
        text = text.replace('      if (test_case == "nospace")',
            '      if (test_case == "joint")\n'
            '        ExerciseColdMapJoint(env.get(), *fs, *device, output, mode == "verify", logger.get());\n'
            '      else if (test_case == "model")\n'
            '        ExerciseColdMapModel(env.get(), *fs, *device, output, mode == "verify");\n'
            '      else if (test_case == "nospace")')
    if method == 'oaza_gc':
        require(text.count('#include "nospace_probe.h"') == 1, 'probe include anchor changed')
        text = text.replace('#include "nospace_probe.h"', '#include "nospace_probe.h"\n#include "oaza_gc_probe.h"\n#include "oaza_middle_probe.h"')
        require(text.count('test_case == "fault", "unknown test case");') == 1, 'case whitelist anchor changed')
        text = text.replace('test_case == "fault", "unknown test case");',
            'test_case == "fault" || test_case == "middle" || test_case == "recovery", "unknown test case");')
        text = text.replace('test_case != "fault" && test_case != "nospace",',
            'test_case != "fault" && test_case != "nospace" && test_case != "middle" && test_case != "recovery",')
        require(text.count('      if (test_case == "nospace")') == 1, 'probe dispatch anchor changed')
        text = text.replace('      if (test_case == "nospace")',
            '      if (test_case == "recovery")\n'
            '        ExerciseOazaGC(env.get(), *fs, *device, output, mode == "verify", logger.get(), true);\n'
            '      else if (test_case == "middle")\n'
            '        ExerciseOazaMiddle(env.get(), *fs, output, mode == "verify");\n'
            '      else if (test_case == "gc")\n'
            '        ExerciseOazaGC(env.get(), *fs, *device, output, mode == "verify", logger.get());\n'
            '      else if (test_case == "nospace")')
    return text.encode()


def build_helper(method):
    # Reuse the fully reviewed compile/ABI/link checks. This is a generated
    # independent helper, not a mutation of the CAZA builder or its source tree.
    text = (CAZA / 'build_guest.py').read_text()
    anchor = "    built = []\n"
    require(text.count(anchor) == 1, 'builder anchor changed')
    text = text.replace(anchor, "    units += [n for n in identity['changed_units'] if n.endswith('.cc') and n not in units]\n" + anchor)
    text = text.replace("dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', ZENFS_RELATED_WORK='caza')",
        "dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', ZENFS_RELATED_WORK=" + repr(method) +
        (", COLDMAP_FACTOR_DIRECTION='artifact-survival')" if method == 'coldmap' else
         ", OAZA_GC_VARIANT='literal')" if method == 'oaza_gc' else ")"))
    text = text.replace('passed=True, affected_units=units,', 'passed=True, method=' + repr(method) + ', affected_units=units,')
    if method == 'oaza_gc':
        text = text.replace("supported_cases=['basic', 'direct', 'rr', 'pressure', 'gc', 'fault', 'nospace']",
                            "supported_cases=['basic', 'direct', 'rr', 'pressure', 'gc', 'fault', 'nospace', 'middle', 'recovery']")
    elif method == 'coldmap':
        text = text.replace("supported_cases=['basic', 'direct', 'rr', 'pressure', 'gc', 'fault', 'nospace']",
                            "supported_cases=['basic', 'direct', 'rr', 'pressure', 'gc', 'fault', 'nospace', 'model', 'joint']")
    else:
        text = text.replace("supported_cases=['basic', 'direct', 'rr', 'pressure', 'gc', 'fault', 'nospace']",
                            "supported_cases=['basic', 'direct', 'rr', 'pressure', 'gc', 'fault', 'nospace', 'joint']")
    return text.encode()


def payload_for(overlay, output, method):
    identity = json.loads((overlay / 'overlay_identity.json').read_text())
    if method == 'caza':
        # The earlier CAZA manifest predates the method field. Admit that exact
        # frozen tree only; absence of a tag is not a general method default.
        frozen = BASE / 'workspaces/caza-v6-20261107'
        require(overlay.resolve() == frozen.resolve() and
                digest(overlay / 'overlay_identity.json') == digest(frozen / 'overlay_identity.json'),
                'CAZA requires the previously verified frozen substrate')
    else:
        require(identity.get('method') == method, 'wrong method overlay')
    src = overlay / 'src/rocksdb'
    require(all(digest(src / n) == h for n, h in identity['source_sha256'].items()), 'overlay identity changed')
    with tarfile.open(output, 'x:gz') as tar:
        tar.add(overlay / 'overlay_identity.json', arcname='overlay_identity.json')
        for name in identity['source_sha256']:
            tar.add(src / name, arcname='src/rocksdb/' + name, recursive=False)
        for name in ('rr_probe.h', 'pressure_probe.h', 'nospace_probe.h', 'fault_io.cc', 'fault_scope_test.py'):
            tar.add(CAZA / name, arcname=name)
        data = probe_source(method)
        member = tarfile.TarInfo('probe.cc'); member.size = len(data); tar.addfile(member, io.BytesIO(data))
        if method == 'oaza_gc':
            tar.add(BASE / 'oaza_gc/gc_probe.h', arcname='oaza_gc_probe.h')
            tar.add(BASE / 'oaza_gc/middle_probe.h', arcname='oaza_middle_probe.h')
        elif method == 'coldmap':
            tar.add(BASE / 'coldmap/model_probe.h', arcname='coldmap_model_probe.h')
            tar.add(BASE / 'coldmap/joint_probe.h', arcname='coldmap_joint_probe.h')
        else:
            tar.add(BASE / 'common/joint_rr_probe.h', arcname='joint_rr_probe.h')
            data = joint_header(method)
            member = tarfile.TarInfo('caza_joint_probe.h'); member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
        data = build_helper(method)
        member = tarfile.TarInfo('build_guest.py'); member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
        tar.add(COMMAND_ROOT / '计时编译-fs_zenfs.命令.json', arcname='compile.original.json')


def execute(args, password, output, payload):
    run = SourceRun(args, password, output, 'compile', 8101, PARENT, retain_image=False)
    run.system_memory_gib = 4; run.fresh_image = True
    configure(run, ftl_audit=False)
    policy = run.state['结果保存策略']
    policy.update(本轮归档含输入字节=False, 输入说明='仅独立编译与POSIX功能校验，无模拟SSD')
    run.record(结果保存策略=policy, 方法=args.method, 边界='独立相关工作编译，非性能实验')
    run.archive_exclusions += ('payload.tar.gz', 'src', 'objects', 'original-*.o',
                               'posix-basic', 'posix-direct', 'posix-pressure', 'fault-scope.bin')
    error = None
    try:
        run.start(); run.ssh('test ! -e ' + run.target + ' && mkdir ' + run.target)
        run.deployed = True
        run.command(run.scp() + [str(payload), 'femu@127.0.0.1:' + run.target + '/payload.tar.gz'])
        require(run.ssh('sha256sum ' + run.target + '/payload.tar.gz').split()[0] == digest(payload), 'payload changed')
        run.ssh('tar -xzf ' + run.target + '/payload.tar.gz -C ' + run.target)
        run.record(阶段='独立编译全部受影响单元、ABI及POSIX功能核验')
        run.ssh('python3 ' + run.target + '/build_guest.py ' + run.target +
                ' > ' + run.target + '/build.log 2>&1', 1800)
        run.record(阶段='独立构建及普通目录校验完成，归档并关机')
    except BaseException as failure:
        error = failure; run.record(原始错误=repr(failure)); raise
    finally:
        run.finish(error)
        saved = run.state.get('镜像处理', {})
        if saved.get('archive') and digest(Path(saved['archive'])) == saved.get('archive_sha256'):
            payload.unlink()
        compact(output)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--method', choices=('coldmap', 'oaza_gc', 'caza'), required=True)
    p.add_argument('--overlay', type=Path, required=True); p.add_argument('--label', required=True)
    p.add_argument('--check', action='store_true'); p.add_argument('--detach', action='store_true')
    a = p.parse_args()
    require(a.label.startswith('joint-related-' + a.method.replace('_', '-') + '-build-') and
            all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in a.label), 'invalid label')
    budget = preflight()
    budget.update(目录已占字节=check_baseline_budget(2 << 30), 新增磁盘预算字节=2 << 30,
                  系统内存字节=4 << 30, 模拟SSD接入=False)
    output = IMAGE_DIR / (a.label + '-results'); require(not output.exists(), 'refuse overwrite')
    if a.check: print(json.dumps(budget, ensure_ascii=False, indent=2)); return
    password = getpass.getpass('独立相关工作构建认证（仅内存）：')
    output.mkdir(); payload = output / 'payload.tar.gz'
    payload_for(a.overlay.resolve(), payload, a.method)
    (output / '构建来源.json').write_text(json.dumps(dict(method=a.method,
        overlay=str(a.overlay.resolve()), identity=digest(a.overlay / 'overlay_identity.json'),
        payload_sha256=digest(payload), resources=budget,
        cleanup='ImageLease archive verification, normal shutdown, temporary image/payload reclamation'),
        ensure_ascii=False, indent=2) + '\n')
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if a.detach and not detach_supervisor(output): return
    try: execute(a, password, output, payload)
    finally: password = None


if __name__ == '__main__': main()
