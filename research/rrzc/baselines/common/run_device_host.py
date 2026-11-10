"""Isolated paper-method device probe; serial lifecycle and strict build admission."""
import argparse
import getpass
import hashlib
import io
import json
from pathlib import Path
import signal
import sys
import tarfile

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path[:0] = [str(HERE), str(BASE)]
from export_source_host import (CalibrationRun, PARENT, IMAGE_DIR, configure, compact, digest,
                                require, preflight, check_baseline_budget, detach_supervisor)
from result_storage import closed_archive, atomic_json
from run_pool_dbbench_host import BINARY
from analyze_placement_integration import analyze as audit
CAZA = BASE / 'caza'
from run_native_host import probe_source, joint_header


def wrapper(method, variant, case=None):
    text = (CAZA / 'device_guest.py').read_text()
    if method == 'oaza_gc': text = text.replace("'fault', 'nospace')", "'fault', 'nospace', 'middle')")
    elif method == 'coldmap': text = text.replace("'fault', 'nospace')", "'fault', 'nospace', 'model')")
    else: text = text.replace("'fault', 'nospace')", "'fault', 'nospace', 'joint')")
    require(text.count("ZENFS_RELATED_WORK='caza'") == 1, 'device helper anchor changed')
    extra = 'COLDMAP_FACTOR_DIRECTION=' if method == 'coldmap' else 'OAZA_GC_VARIANT='
    text = text.replace("ZENFS_RELATED_WORK='caza'", 'ZENFS_RELATED_WORK=' + repr(method) +
                        (', ' + extra + repr(variant) if method != 'caza' else ''))
    if case in ('joint', 'recovery'):
        from joint_checks import checks
        text = text.replace("'middle')", "'middle', 'recovery')") if method == 'oaza_gc' else text.replace("'model')", "'model', 'joint')")
        text = text.replace("test_case == 'gc'", "test_case in ('gc', 'joint', 'recovery')")
        text = text.replace("('gc', 'nospace')", "('gc', 'nospace', 'joint', 'recovery')")
        start = text.index("        assert 'CAZA target: stage=empty' in log")
        end = text.index('    finally:\n', start)
        return (text[:start] + checks(method, variant, case) + text[end:]).encode()
    if method == 'coldmap':
        anchor = "        assert '协同池取得目标：' not in log"
        require(text.count(anchor) == 1, 'device check anchor changed')
        text = text.replace(anchor, '''        if test_case == 'gc':
            assert 'ColdMap selected: zone=' in log and 'ColdMap frame:' in log
            assert 'ColdMap deferred:' not in log, 'ColdMap metadata/model failed during this fixture'
            assert '简单双因素实际集合：' not in log, 'original model selected the victim'
            gc_result['coldmap_selected'] = True
            gc_result['factor_direction'] = ''' + repr(variant) + '''
        if test_case == 'model':
            with (target / 'coldmap-model.csv').open() as stream: model_rows = list(csv.DictReader(stream))
            assert len(model_rows) == 1
            model = model_rows[0]
            assert model['compactions'] == '3' and model['depth'] == '3' and model['steps'] == '3'
            assert int(model['created']) > 0 and int(model['generated_pivots']) > 0
            assert model['direction'] == ''' + repr(variant) + '''
''' + anchor)
    else:
        # The common client validates actual data/checksums/native mappings.
        # CAZA-specific nearest/overlap assertions would mislabel OAZA behavior.
        start = text.index("        assert 'CAZA target: stage=empty' in log")
        end = text.index('    finally:\n', start)
        text = text[:start] + '''        assert 'OAZA target:' in log or 'OAZA fallback:' in log
        assert 'CAZA target:' not in log and '协同池取得目标：' not in log
        physical_equal = (target / 'device-mapping.csv').read_bytes() == (target / 'verify-mapping.csv').read_bytes()
        def logical_map(name):
            result = {}
            with (target / name).open() as stream:
                for row in csv.DictReader(stream):
                    key = (row['sst'], row['level'])
                    result[key] = result.get(key, 0) + int(row['length'])
            return result
        if test_case == 'middle':
            assert (target / 'device-mapping.csv').read_bytes() == (target / 'verify-before-write.csv').read_bytes()
        else:
            assert logical_map('device-mapping.csv') == logical_map('verify-mapping.csv')
            if test_case != 'gc': assert physical_equal
        with (target / 'device-mapping.csv').open() as stream:
            mapping = list(csv.DictReader(stream))
        assert mapping
        middle_verified = False
        if test_case == 'middle':
            assert {row['level'] for row in mapping} == {'2', '3'}
            assert 'OAZA target: stage=fresh-promote' in log and 'OAZA target: stage=ratio' in log
            with (target / 'oaza-middle-stages.csv').open() as stream: stages = list(csv.DictReader(stream))
            assert len(stages) == 3 and stages[0]['L2'] == '0' and int(stages[0]['L3']) > 0
            assert all(int(row['L2']) > 0 and int(row['L3']) > 0 for row in stages[1:])
            middle_verified = True
        else: assert any(row['level'] == '1' for row in mapping)
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == proof['binary_sha256']
        gc_verified = False
        gc_details = None
        if test_case == 'gc':
            assert 'OAZA-GC deferred:' not in log and 'ZC 迁移失败：' not in log
            assert 'RR 完成：' not in log and '简单双因素实际集合：' not in log
            frames = re.findall(r'OAZA-GC frame: invalid=(\\d+) allocated=(\\d+) zones=(\\d+) requested=(\\d+) selected=(\\d+) variant=(\\S+)', log)
            assert frames and any(int(row[4]) > 0 for row in frames)
            for invalid, allocated, zones, requested, selected, actual_variant in frames:
                expected_quantity = (int(invalid) * int(zones) * 3) // (int(allocated) * 10)
                assert int(requested) == expected_quantity and int(selected) <= int(requested)
                assert actual_variant == ''' + repr(variant) + '''
            reset_count = int((target / 'oaza-gc-reset.txt').read_text())
            assert reset_count > 0
            gc_verified = True
            gc_details = dict(frames=frames, source_zones_reset=reset_count,
                quantity_formula_verified=True, no_rr_observed=True)
        (target / 'device_complete.json').write_text(json.dumps(dict(
            passed=True, method='oaza_gc', variant=''' + repr(variant) + ''', case=test_case,
            latest_values_verified=True, checksums_verified=True, remounted=True,
            native_live_mapping_verified=True, placement_verified=True,
            physical_mapping_equal=physical_equal,
            middle_level_ratio_and_promotion_verified=middle_verified,
            gc_enabled=test_case == 'gc', gc_device_verified=gc_verified, gc=gc_details,
            performance_experiment=False,
            limitations=['selected functional case only; not the full paper workload',
                         'functional validation only; not a performance comparison']), indent=2) + '\\n')
''' + text[end:]
    return text.encode()


def prepare(build, payload, method, variant, case):
    state, archive = closed_archive(build)
    folder = build / state['标签']
    require(json.loads((build / '阶段归档.json').read_text())['业务成功'], 'failed build')
    names = ['caza_probe', 'build_complete.json', 'overlay_identity.json', 'core_abi_check.json',
             'build.log', 'posix-smoke.log', 'probe.cc', 'rr_probe.h', 'pressure_probe.h', 'nospace_probe.h', 'fault_io.cc']
    if case in ('gc', 'pressure', 'joint', 'recovery'): names += ['posix-pressure-smoke.log']
    if case == 'direct': names += ['posix-direct-smoke.log']
    if method == 'oaza_gc': names += ['oaza_gc_probe.h', 'oaza_middle_probe.h']
    elif method == 'coldmap': names += ['coldmap_model_probe.h', 'coldmap_joint_probe.h']
    else: names += ['caza_joint_probe.h', 'joint_rr_probe.h']
    expected = {n: digest(folder / n) for n in names}
    found = set()
    with tarfile.open(archive) as tar:
        for member in tar:
            prefix = state['标签'] + '/'
            if not member.name.startswith(prefix): continue
            name = member.name[len(prefix):]
            if name not in expected: continue
            require(member.isfile() and name not in found, 'invalid/duplicate evidence member')
            require(hashlib.sha256(tar.extractfile(member).read()).hexdigest() == expected[name], 'modified build evidence: ' + name)
            found.add(name)
    require(found == set(expected), 'incomplete archived build')
    proof = json.loads((folder / 'build_complete.json').read_text())
    require(proof['method'] == method and proof['passed'] and proof['NDEBUG'] and
            proof['known_failing_member_offsets_match'] and digest(folder / 'caza_probe') == proof['binary_sha256'],
            'build/ABI/binary admission failed')
    require(digest(folder / 'probe.cc') == hashlib.sha256(probe_source(method)).hexdigest(), 'stale generated client')
    for n in ('rr_probe.h', 'pressure_probe.h', 'nospace_probe.h', 'fault_io.cc'):
        require(digest(folder / n) == digest(CAZA / n), 'stale client protocol: ' + n)
    if method == 'oaza_gc':
        require(digest(folder / 'oaza_gc_probe.h') == digest(BASE / 'oaza_gc/gc_probe.h'), 'stale OAZA GC client')
        require(digest(folder / 'oaza_middle_probe.h') == digest(BASE / 'oaza_gc/middle_probe.h'), 'stale OAZA middle client')
    elif method == 'coldmap':
        require(digest(folder / 'coldmap_model_probe.h') == digest(BASE / 'coldmap/model_probe.h'), 'stale ColdMap model client')
        require(digest(folder / 'coldmap_joint_probe.h') == digest(BASE / 'coldmap/joint_probe.h'), 'stale ColdMap joint client')
    else:
        require((folder / 'caza_joint_probe.h').read_bytes() == joint_header(method), 'stale CAZA joint client')
        require(digest(folder / 'joint_rr_probe.h') == digest(BASE / 'common/joint_rr_probe.h'), 'stale joint RR client')
    require('posix-smoke passed' in (folder / 'build.log').read_text() and
            'CAZA posix latest values/checksums/reopen passed' in (folder / 'posix-smoke.log').read_text(), 'POSIX failed')
    if case in ('gc', 'pressure', 'joint', 'recovery'): require('posix-pressure-smoke passed' in (folder / 'build.log').read_text(), 'pressure POSIX failed')
    if case == 'direct': require('posix-direct-smoke passed' in (folder / 'build.log').read_text(), 'direct POSIX failed')
    with tarfile.open(payload, 'x:gz') as tar:
        for n in ('caza_probe', 'build_complete.json', 'overlay_identity.json'):
            tar.add(folder / n, arcname=n, recursive=False)
        data_items = {'device_guest.py': wrapper(method, variant, case),
            'test_case.json': json.dumps(dict(case=case)).encode(),
            'posix_complete.json': json.dumps(dict(passed=True, source_log_sha256=digest(folder / 'posix-smoke.log'),
                build_log_sha256=digest(folder / 'build.log'))).encode()}
        for n, data in data_items.items():
            item = tarfile.TarInfo(n); item.size = len(data); tar.addfile(item, io.BytesIO(data))
    return dict(archive_sha256=digest(archive), binary_sha256=proof['binary_sha256'], verified_files=expected)


def execute(args, password, output, payload):
    run = CalibrationRun(args, password, output, 'device', 8101, PARENT, retain_image=False)
    run.fresh_image = True; run.ftl_audit_binary = str(BINARY); run.ftl_audit_sha256 = digest(BINARY)
    configure(run, ftl_audit=False)
    run.record(方法=args.method, 变体=args.variant, 测试用例=args.case,
               GC启用=args.case in ('gc', 'joint', 'recovery'), 边界='隔离设备功能测试，非收益对照')
    policy = run.state['结果保存策略']
    policy.update(本轮归档含输入字节=False, 输入说明='固定可生成Key/Value及填充清单，无完整NAND检查点')
    atomic_json(output / '结果保存策略.json', policy)
    run.archive_exclusions += ('payload.tar.gz',)
    error = None
    try:
        run.start(); run.ssh('test ! -e ' + run.target + ' && mkdir ' + run.target); run.deployed = True
        run.command(run.scp() + [str(payload), 'femu@127.0.0.1:' + run.target + '/payload.tar.gz'])
        require(run.ssh('sha256sum ' + run.target + '/payload.tar.gz').split()[0] == digest(payload), 'payload changed')
        run.ssh('tar -xzf ' + run.target + '/payload.tar.gz -C ' + run.target)
        run.record(阶段='核验全空模拟设备，真实功能与重挂载测试')
        run.root('python3 ' + run.target + '/device_guest.py ' + run.target +
                 ' > ' + run.target + '/device-wrapper.log 2>&1', 900 if args.case in ('gc', 'joint', 'recovery') else 420)
        run.record(阶段='设备验证完成，归档并正常关机')
    except BaseException as failure:
        error = failure; run.record(原始错误=repr(failure)); raise
    finally:
        run.finish(error)
        saved = run.state.get('镜像处理', {})
        if saved.get('archive') and digest(Path(saved['archive'])) == saved.get('archive_sha256'): payload.unlink()
        compact(output)
    reports = []
    for mode in ('device', 'verify'):
        report = audit(output / args.label / (mode + '-composition.bin'))
        reports.append(report)
        atomic_json(output / (mode + '-composition-audit.json'), report)
        require(report['事件账目闭合'], 'event accounting did not close')
    if (args.method == 'oaza_gc' and args.case == 'gc') or args.case in ('joint', 'recovery'):
        proof = mapping_transition(output / args.label, reports)
        atomic_json(output / 'physical-mapping-audit.json', proof)
        require(proof['passed'], 'physical mapping change not explained by committed copies')
    run.record(阶段='归档哈希、退出和逐笔事件账目通过')


def mapping_transition(folder, reports):
    import csv
    def load(name):
        with (folder / name).open() as f:
            return sorted((int(r['sst']), int(r['level']), int(r['zone']), int(r['start']), int(r['length']))
                          for r in csv.DictReader(f))
    after_name = 'verify-before-write.csv' if (folder / 'verify-before-write.csv').exists() else 'verify-mapping.csv'
    before, after = load('device-mapping.csv'), load(after_name)
    copies = [(mode, c) for mode, report in zip(('device', 'verify'), reports)
              for c in report['复制明细'] if c['映射结局'] == '提交']
    # Either CSV may precede later maintenance in its process. Find a contiguous
    # committed-copy interval, reporting both excluded ends. Complete trace
    # accounting is checked separately; this is not an atomic export timestamp.
    def result(start, end, changed):
        return dict(passed=True, physical_equal=before == after, compared_after=after_name,
                    committed_copy_suffix_start=start, committed_copy_interval_end=end,
                    total_committed_copies=len(copies), later_committed_copies=len(copies) - end,
                    later_committed_bytes=sum(c['有效复制字节'] for _, c in copies[end:]),
                    explained_extent_changes=changed,
                    boundary='contiguous committed-copy interval closes CSV difference; complete trace accounting is separate; no atomic CSV timestamp')
    for start in range(len(copies) + 1):
        current = list(before); changed = []
        if sorted(current) == after: return result(start, start, changed)
        for end, (mode, c) in enumerate(copies[start:], start + 1):
            source = (c['源Zone'], c['源片段'], c['有效复制字节'])
            for i, row in enumerate(current):
                if row[2:] == source:
                    current[i] = row[:2] + (c['目标Zone'], c['目标片段'], row[4])
                    changed.append(dict(mode=mode, event=c['事件'], sst=row[0],
                        old=list(source), new=list(current[i][2:])))
            if sorted(current) == after: return result(start, end, changed)
    return dict(passed=False, physical_equal=False, reason='no contiguous committed-copy interval closes native mapping difference')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--method', choices=('coldmap', 'oaza_gc', 'caza'), required=True)
    p.add_argument('--variant', required=True); p.add_argument('--case', choices=('basic', 'direct', 'pressure', 'gc', 'middle', 'model', 'joint', 'recovery'), required=True)
    p.add_argument('--build', type=Path, required=True); p.add_argument('--label', required=True)
    p.add_argument('--detach', action='store_true'); p.add_argument('--check', action='store_true'); a = p.parse_args()
    require(a.variant in (('printed', 'artifact-survival') if a.method == 'coldmap' else
                         ('literal', 'adapted', 'prose') if a.method == 'oaza_gc' else ('native',)), 'invalid variant')
    require(a.case != 'middle' or a.method == 'oaza_gc', 'middle fixture requires OAZA')
    require(a.case != 'model' or a.method == 'coldmap', 'model fixture requires ColdMap')
    require(a.case != 'joint' or a.method in ('coldmap', 'caza'), 'joint fixture requires ColdMap/CAZA')
    require(a.case != 'recovery' or a.method == 'oaza_gc', 'recovery fixture requires OAZA')
    require(a.method != 'caza' or a.case == 'joint', 'common CAZA entry currently supports only joint')
    require(a.label.startswith('joint-related-' + a.method.replace('_', '-') + '-device-') and
            all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in a.label), 'invalid label')
    budget = preflight()
    budget.update(目录已占字节=check_baseline_budget(1 << 30), 新增磁盘预算字节=1 << 30,
                  总内存预算字节=12 << 30, 并发客机数=1)
    output = IMAGE_DIR / (a.label + '-results'); require(not output.exists(), 'refuse overwrite')
    if a.check: print(json.dumps(budget, ensure_ascii=False, indent=2)); return
    password = getpass.getpass('独立相关工作设备认证（仅内存）：')
    output.mkdir(); payload = output / 'payload.tar.gz'
    proof = prepare(a.build.resolve(), payload, a.method, a.variant, a.case)
    atomic_json(output / '来源及计划.json', dict(method=a.method, variant=a.variant, case=a.case, build=proof,
        payload_sha256=digest(payload), resources=budget, cleanup='archive, normal shutdown, verify exit, reclaim'))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if a.detach and not detach_supervisor(output): return
    try: execute(a, password, output, payload)
    finally: password = None


if __name__ == '__main__': main()
