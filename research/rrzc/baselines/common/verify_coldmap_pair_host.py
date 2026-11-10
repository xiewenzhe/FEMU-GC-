"""Independently recheck both corrected full ColdMap arms and immutable evidence."""
import hashlib
import json
from pathlib import Path
import tarfile

from run_coldmap_survival_host import PREVIOUS, LABEL, REFERENCE
from run_comparison_host import IMAGE_DIR, closed_archive, digest, atomic_json, admit_build
from analyze_comparison import analyze_arm, compare
from comparison_source import BASE, METHODS
from cache_config_guard import check as cache_check


def verify():
    root = IMAGE_DIR / (LABEL + '-results')
    assert json.loads((root / 'comparison-state.json').read_text())['stage'] == 'corrected_coldmap_pair_verified'
    reports = {}; folders = {}; archives = {}
    for arm, parent in [('cold-printed', PREVIOUS / 'cold-printed'), ('cold-survival', root / 'cold-survival')]:
        saved, archive = closed_archive(parent)
        assert json.loads((parent / '阶段归档.json').read_text())['业务成功']
        lease = json.loads((parent / '镜像生命周期.json').read_text())
        assert lease['status'] == 'deleted' and not Path(lease['image']).exists()
        assert not (parent / 'payload.tar.gz').exists()
        folder = parent / saved['标签']; folders[arm] = folder
        files = {}
        with tarfile.open(archive, 'r|gz') as tar:
            prefix = saved['标签'] + '/'
            for member in tar:
                if not member.name.startswith(prefix) or not member.isfile(): continue
                name = member.name[len(prefix):]
                assert name and not Path(name).is_absolute() and '..' not in Path(name).parts
                local = folder / name
                assert local.is_file() and not local.is_symlink() and name not in files
                with tar.extractfile(member) as stream:
                    h = hashlib.sha256()
                    for block in iter(lambda: stream.read(1 << 20), b''): h.update(block)
                assert h.hexdigest() == digest(local), str(local)
                files[name] = h.hexdigest()
        report = analyze_arm(folder, 12)
        assert json.loads(json.dumps(report)) == json.loads((parent / 'comparison-audit.json').read_text())
        assert report['coldmap_deferred'] == 0
        safety = {}
        for phase in ('load', 'run', 'verify', 'reopen'):
            assert (folder / (phase + '.exit')).read_text().strip() == '0'
            snapshot = json.loads((folder / (phase + '-end.json')).read_text())
            assert snapshot['strict']['历史最高RC'] <= 1024 and all(b['RC'] <= 1024 for b in snapshot['blocks'])
            assert report['trace_receipts'][phase]['原生映射核验次数'] > 0
            safety[phase] = dict(highest_rc=snapshot['strict']['历史最高RC'],
                cache=cache_check((folder / (phase + '.rocksdb.log')).read_text(), 1))
        reports[arm] = report
        archives[arm] = dict(path=str(archive), sha256=digest(archive), files=files, safety=safety)
    cross = compare(folders['cold-printed'], folders['cold-survival'], reports['cold-printed'], reports['cold-survival'])
    assert cross == json.loads((root / 'printed-survival-audit.json').read_text())
    state, _ = closed_archive(REFERENCE / 'g2'); g2 = REFERENCE / 'g2' / state['标签']
    g2_report = json.loads((REFERENCE / 'g2/comparison-audit.json').read_text())
    reference_checks = {arm: compare(g2, folders[arm], g2_report, report) for arm, report in reports.items()}
    _, build = admit_build(PREVIOUS / 'build', 'coldmap')
    assert all(digest(folder / 'comparison_db_bench') == build['binary_sha256'] for folder in folders.values())
    unchanged = {}
    for tree in [*METHODS.values(), 'coldmap-v6-20261109']:
        source = BASE / 'workspaces' / tree
        identity = json.loads((source / 'overlay_identity.json').read_text())
        assert all(digest(source / 'src/rocksdb' / n) == sha for n, sha in identity['source_sha256'].items())
        unchanged[tree] = len(identity['source_sha256'])
    repo = BASE.parents[2]
    four = repo / 'research/rrzc/results/placement-ablation-20261105/阶段四'
    four_hashes = json.loads((four / '交付文件SHA256.json').read_text())
    assert all(digest(four / n) == sha for n, sha in four_hashes.items())
    five = json.loads((BASE / 'workspaces/caza-v6-20261107/本轮设备核验.json').read_text())['stage_five_current_hashes']
    assert all(digest(repo / n) == sha for n, sha in five.items())
    auth = json.loads((root / 'cold-survival/启动认证核验.json').read_text())
    assert auth['passed'] and auth['legacy_socket_probe']['reproduced']
    result = dict(passed=True, reports=reports, archives=archives, same_input=cross,
        reference_checks=reference_checks, binary_sha256=build['binary_sha256'], startup=auth,
        frozen_source_files=unchanged, stage_four_unchanged_files=len(four_hashes),
        stage_five_unchanged_files=len(five), all_methods_complete=False,
        full_coldmap_pair_verified=True, statistical_performance_claim=False,
        boundary='one full input per variant; CRC32C is not bytewise evidence; no atomic post-load NAND checkpoint')
    output = BASE / 'workspaces/comparison-20261109-v1/ColdMap两版整轮最终核验.json'
    assert not output.exists(); atomic_json(output, result)
    print(output, flush=True)


if __name__ == '__main__': verify()
