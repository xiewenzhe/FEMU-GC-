"""Shared-input admission and full per-phase maintenance accounting."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1]), str(HERE.parents[1] / 'tests')]
from analyze_integrated_pool import planned
from analyze_reproducibility import request_rows
import analyze_end_to_end
from verify_paper_runs import phase_summary
from run_device_host import audit


def read(path): return json.loads(path.read_text())


def application_audit(folder, windows):
    # Keep the original per-request/Flush checks byte-for-byte. Only the
    # pre-registered expected counts differ for a finite full-size prefix.
    import inspect
    text = inspect.getsource(analyze_end_to_end.analyze)
    expected = '{0:786432,1:1474560}'
    assert text.count(expected) == 1 and 1 <= windows <= 12
    text = text.replace(expected, repr({0: windows * 65536, 1: windows * 122880}))
    namespace = dict(vars(analyze_end_to_end))
    exec(compile(text, str(Path(__file__).resolve()) + ':count-adapter', 'exec'), namespace)
    return namespace['analyze'](folder)


def request_proof(path, windows):
    wanted = iter(planned(windows)); count = [0, 0]; digest = hashlib.sha256()
    import struct
    for n, row in enumerate(request_rows(path)):
        pair = next(wanted, None)
        assert pair and row[:4] == (n, pair[0], count[pair[0]], pair[1]), 'request first difference: ' + str(n)
        digest.update(struct.pack('<4Q', *row[:4])); count[pair[0]] += 1
    assert next(wanted, None) is None and count == [windows * 65536, windows * 122880]
    return dict(count=sum(count), reads=count[0], writes=count[1], logical_sha256=digest.hexdigest())


def initial_semantics(initial):
    zones = [(z['slba'], z['wp'], z['state'], z['cap']) for z in initial['zones']['zone_list']]
    fields = ('Zone', '块', '代际', 'RC', '十秒增量', '三十秒增量', 'Channel', 'LUN', 'Plane', '物理块', '标志')
    blocks = [tuple(b[k] for k in fields) for b in initial['blocks']]
    return dict(zones=zones, blocks=blocks, identify=initial['identify'], strict=initial['strict'])


def analyze_arm(folder, windows):
    proof = read(folder / 'comparison_complete.json')
    assert proof['passed'] and proof['windows'] == windows and proof['reopened_digest_equal']
    requests = request_proof(folder / 'requests.bin', windows)
    with tempfile.TemporaryDirectory(prefix='rrzc-common-audit-') as temp:
        adapter = Path(temp)
        for alias, name in [('请求提交.bin', 'requests.bin'), ('应用完成.bin', 'application.bin'), ('run.log', 'run.log')]:
            (adapter / alias).symlink_to((folder / name).resolve())
        application = application_audit(adapter, windows)
        assert application['完成请求数'] == requests['count']
        phases = {}; receipts = {}
        for name in ('load', 'run', 'verify', 'reopen'):
            trace = audit(folder / (name + '-composition.bin'))
            assert trace['事件账目闭合']
            (adapter / ('zenfs-' + name + '.log')).symlink_to((folder / (name + '.zenfs.log')).resolve())
            phases[name] = phase_summary(adapter, name, trace)
            receipts[name] = dict(事件账目闭合=True, 有效复制字节=trace['有效复制字节'],
                                  原生映射核验次数=trace['原生映射核验次数'], 各类事件=trace['各类事件'])
    logs = '\n'.join((folder / (p + '.zenfs.log')).read_text() for p in phases)
    if proof['arm'] == 'g2':
        assert '模式=G2' in logs and 'CAZA target:' not in logs
    else:
        assert '协同池取得目标：' not in logs
        assert ('OAZA target:' in logs or 'OAZA fallback:' in logs) if proof['arm'].startswith('oaza') else 'CAZA target:' in logs
    assert 'ZC 迁移失败：' not in logs
    return dict(passed=True, arm=proof['arm'], requests=requests, application=application,
                phases=phases, trace_receipts=receipts, generated_input=proof['input_generated'],
                final_digest=proof['final_digest'], client_files=proof['common_client_files'],
                coldmap_deferred=logs.count('ColdMap deferred:'), oaza_deferred=logs.count('OAZA-GC deferred:'),
                initial_state_verified='empty independent device; no restored NAND snapshot',
                performance_claim=False)


def compare(folder_a, folder_b, report_a, report_b):
    assert report_a['requests'] == report_b['requests'], 'logical request stream differs'
    assert report_a['client_files'] == report_b['client_files'], 'shared client source differs'
    assert read(folder_a / 'common-options.json') == read(folder_b / 'common-options.json'), 'common options differ'
    assert initial_semantics(read(folder_a / 'initial.json')) == initial_semantics(read(folder_b / 'initial.json')), 'fresh-device state differs'
    assert report_a['generated_input'] == report_b['generated_input'], 'generated load values differ'
    assert report_a['final_digest'] == report_b['final_digest'], 'final logical database digest differs'
    return dict(passed=True, exact_logical_requests=True, generated_load_digest_equal=True,
                final_logical_digest_equal=True, empty_device_semantics_equal=True,
                layout_after_load_may_differ=True,
                unconfirmed=['no cross-layer atomic post-load checkpoint', 'CRC32C is a checksum, not bytewise value evidence'],
                performance_claim=False)
